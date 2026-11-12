"""The ticket routine of `briar serve` (spec 003 PR 2; decisions S18 to S27):

plan -> comment on the ticket and move it -> build -> verify loop -> self-review loop ->
push and open the PR, ready -> comment and move -> wait for CI, fixing a failure.

The agent only edits (D10, S25): commits, push, PR, comments and status are this code.
When a limit hits (S23) the work is pushed, the PR becomes a draft with a report, and the
ticket gets the report too."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, ClassVar

from briar.errors import CliError
from briar.extract._provider import CiState, PullRequestDraft
from briar.serve.agent import AgentRunner
from briar.serve.state import WorkEntry, WorkSource, WorkStatus
from briar.serve.task import AgentSteps, Budget, LimitHit, Plan, TaskContext, TaskPrep
from briar.serve.git import Shell
from briar.serve.work import ServeCompany, WorkItem, WorkKind, WorkSources


class TicketSide:
    """What briar writes on a ticket: comments (S19) and the status (S26)."""

    def __init__(self, sources: WorkSources) -> None:
        self._sources = sources

    def comment(self, item: WorkItem, text: str) -> None:
        if item.source is WorkSource.GITHUB:
            self._sources.github().comment(item.repo, int(item.number()), text)
            return
        self._sources.jira().comment(item.key, text)

    def move(self, item: WorkItem, status: str) -> None:
        """Only Jira has a status to move; no name in the runbook: no move."""
        if status and item.source is WorkSource.JIRA:
            self._sources.jira().move(item.key, status)


@dataclass(frozen=True)
class Progress:
    """What the state holds of a task besides its status."""

    plan: str = ""
    pr: int = 0
    report: str = ""


class TicketRun:
    """One ticket, from the plan to green CI."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    # Seconds between two looks at CI, and before "no check at all" means no CI.
    CI_POLL_SECONDS = 30
    CI_GRACE_SECONDS = 60
    SECONDS_PER_MINUTE = 60
    # The tail of a failed verify output the agent gets.
    OUTPUT_TAIL = 4000

    def __init__(self, context: TaskContext, steps: AgentSteps, budget: Budget) -> None:
        self._context = context
        self._steps = steps
        self._budget = budget

    async def build(self) -> None:
        plan = await self._steps.plan()
        await asyncio.to_thread(self._announce, plan)
        await self._record(WorkStatus.PLANNED, Progress(plan.markdown()))
        await self._steps.build(plan)
        await self._verified()
        await self._reviewed(plan)
        number = await asyncio.to_thread(self._publish, plan)
        await self._record(WorkStatus.PR_OPEN, Progress(plan.markdown(), number))
        await self._ci(number)
        await self._record(WorkStatus.DONE, Progress(plan.markdown(), number))

    async def blocked(self, report: str) -> None:
        """S23: push what there is, a draft PR with the report, the report on the ticket.
        The ticket is blocked even when posting fails (else the next cycle would redo it,
        and pay again): the failure joins the report in the state and the log."""
        text = f"briar stopped: {report}. Spent {self._budget.summary()}."
        known = self._context.company.state.book().entry(self._context.item.key)
        number = known.pr if known is not None else 0
        try:
            number = await asyncio.to_thread(self._draft, text)
        except CliError as failure:
            text = f"{text} The draft PR failed: {failure}."
        try:
            await asyncio.to_thread(TicketSide(self._sources()).comment, self._context.item, text)
        except CliError as failure:
            text = f"{text} The ticket comment failed: {failure}."
        await self._record(WorkStatus.BLOCKED, Progress(pr=number, report=text))

    async def _verified(self) -> None:
        """Commit, run the verify commands, and fix until they pass (S20)."""
        await asyncio.to_thread(self._commit, "build the plan")
        failures = await asyncio.to_thread(self._verify_failures)
        while failures:
            self._budget.next_loop("the verify commands")
            await self._steps.fix("the verify commands fail", failures)
            await asyncio.to_thread(self._commit, "fix the verify commands")
            failures = await asyncio.to_thread(self._verify_failures)

    async def _reviewed(self, plan: Plan) -> None:
        review = await self._steps.review(plan, await self._diff())
        while not review.approved:
            self._budget.next_loop("the self-review")
            await self._steps.fix("the self-review found problems", "\n".join(f"- {finding}" for finding in review.findings))
            await self._verified()
            review = await self._steps.review(plan, await self._diff())

    async def _diff(self) -> str:
        return await asyncio.to_thread(self._context.checkout.diff, self._context.worktree, self._context.base)

    async def _ci(self, number: int) -> None:
        deadline = time.monotonic() + self._context.company.settings.ci_wait_minutes * self.SECONDS_PER_MINUTE
        started = time.monotonic()
        while True:
            state = await asyncio.to_thread(self._sources().github().ci_state, self._context.item.repo, number)
            if state is CiState.PASSED or (state is CiState.NONE and time.monotonic() - started > self.CI_GRACE_SECONDS):
                return
            if state is CiState.FAILED:
                await self._fix_ci(number)
            if time.monotonic() > deadline:
                raise LimitHit(f"CI did not pass within ci_wait_minutes ({self._context.company.settings.ci_wait_minutes})")
            await asyncio.sleep(self.CI_POLL_SECONDS)

    async def _fix_ci(self, number: int) -> None:
        self._budget.next_loop("CI")
        failures = await asyncio.to_thread(self._sources().github().list_ci_failures, self._context.item.repo, number)
        details = "\n\n".join(f"{failure.job} / {failure.step} ({failure.url}):\n{failure.log_tail}" for failure in failures)
        await self._steps.fix("CI fails on the pull request", details or "(no failure log was found)")
        await self._verified()
        await asyncio.to_thread(self._context.checkout.push, self._context.worktree, self._context.branch, self._context.token)

    def _announce(self, plan: Plan) -> None:
        side = TicketSide(self._sources())
        side.comment(self._context.item, f"briar plan for {self._context.item.ref()}:\n\n{plan.markdown()}")
        side.move(self._context.item, self._context.company.settings.status.started)

    def _publish(self, plan: Plan) -> int:
        self._context.checkout.push(self._context.worktree, self._context.branch, self._context.token)
        draft = PullRequestDraft(self._context.branch, self._context.base, self._context.title, self._body(plan))
        number = int(self._sources().github().open_pull(self._context.item.repo, draft))
        side = TicketSide(self._sources())
        side.comment(self._context.item, f"briar opened https://github.com/{self._context.item.repo}/pull/{number}")
        side.move(self._context.item, self._context.company.settings.status.review)
        return number

    def _draft(self, text: str) -> int:
        """The PR of the task as a draft with the report: the open one, or a new one."""
        known = self._context.company.state.book().entry(self._context.item.key)
        if not self._context.checkout.commit_all(self._context.worktree, f"{self._context.item.ref()}: work so far", self._context.identity) and not known:
            return 0
        self._context.checkout.push(self._context.worktree, self._context.branch, self._context.token)
        github = self._sources().github()
        if known is not None and known.pr:
            github.make_draft(self._context.item.repo, known.pr)
            github.comment(self._context.item.repo, known.pr, text)
            return known.pr
        draft = PullRequestDraft(self._context.branch, self._context.base, self._context.title, text, draft=True)
        return int(github.open_pull(self._context.item.repo, draft))

    def _body(self, plan: Plan) -> str:
        closes = f"\n\nCloses #{self._context.item.number()}" if self._context.item.source is WorkSource.GITHUB else f"\n\nTicket: {self._context.item.key}"
        return f"{plan.markdown()}{closes}\n\nBuilt by briar (spent {self._budget.summary()})."

    def _commit(self, what: str) -> None:
        self._context.checkout.commit_all(self._context.worktree, f"{self._context.item.ref()}: {what}", self._context.identity)

    def _verify_failures(self) -> str:
        shell = Shell()
        found = [(command, shell.line(command, self._context.worktree)) for command in self._context.company.settings.verify]
        return "\n\n".join(f"`{command}` failed (exit {done.code}):\n{done.output[-self.OUTPUT_TAIL :]}" for command, done in found if not done.ok())

    def _sources(self) -> WorkSources:
        return self._context.sources

    async def _record(self, status: WorkStatus, progress: Progress) -> None:
        item = self._context.item
        entry = WorkEntry(
            key=item.key, source=item.source, repo=item.repo, title=item.title, status=status, branch=self._context.branch,
            plan=progress.plan, pr=progress.pr, report=progress.report,
        )  # fmt: skip
        await self._context.company.state.record(entry)
        link = f" https://github.com/{item.repo}/pull/{progress.pr}" if progress.pr else ""
        self.LOG.info("serve: %s is %s%s %s", item.ref(), status.value, link, progress.report)


class TicketRoutine:
    """`Routine` of a company: the ticket routine for tickets; reviews wait for spec 003 PR 3."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)

    def __init__(self, company: ServeCompany, agents: Callable[[TaskContext], AgentRunner], prep: TaskPrep) -> None:
        self._company = company
        self._agents = agents
        self._prep = prep

    async def work_on(self, item: WorkItem) -> None:
        if item.kind is WorkKind.REVIEW:
            self.LOG.info("serve: %d new comments on %s#%d (the review routine is not built yet)", len(item.comments), item.repo, item.pr)
            return
        context = await asyncio.to_thread(self._prep.context, self._company, item)
        budget = Budget(self._company.settings.max_usd, self._company.settings.max_loops)
        run = TicketRun(context, AgentSteps(self._agents(context), context, budget), budget)
        try:
            await run.build()
        except CliError as stop:
            await run.blocked(str(stop))


__all__ = ["Progress", "TicketRoutine", "TicketRun", "TicketSide"]
