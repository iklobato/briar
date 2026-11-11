"""The steps both routines of `briar serve` share (spec 003 PR 2 and PR 3): the verify
loop (S20), the CI wait with fixes (S20), what briar writes on the ticket (S19, S26), the
stop on a limit (S23) and the record in the state (S17).

Every GitHub comment briar posts carries the hidden marker (V2)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, ClassVar, Mapping

from briar.errors import CliError
from briar.extract._provider import CiState, PullRequestDraft
from briar.serve.git import Shell
from briar.serve.state import WorkEntry, WorkSource, WorkStatus
from briar.serve.task import AgentSteps, Budget, LimitHit, Plan, TaskContext
from briar.serve.work import Marker, WorkItem, WorkSources


class TicketSide:
    """What briar writes on a ticket: comments (S19) and the status (S26)."""

    def __init__(self, sources: WorkSources) -> None:
        self._sources = sources

    def comment(self, item: WorkItem, text: str) -> None:
        if item.source is WorkSource.GITHUB:
            self._sources.github().comment(item.repo, int(item.number()), Marker().mark(text))
            return
        self._sources.jira().comment(item.key, text)

    def move(self, item: WorkItem, status: str) -> None:
        """Only Jira has a status to move; no name in the runbook: no move."""
        if status and item.source is WorkSource.JIRA:
            self._sources.jira().move(item.key, status)


class TaskFlow:
    """The shared steps of one task of a company."""

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

    async def verified(self, what: str) -> None:
        """Commit, run the verify commands, and fix until they pass (S20)."""
        await asyncio.to_thread(self.commit, what)
        failures = await asyncio.to_thread(self._verify_failures)
        while failures:
            self._budget.next_loop("the verify commands")
            await self._steps.fix("the verify commands fail", failures)
            await asyncio.to_thread(self.commit, "fix the verify commands")
            failures = await asyncio.to_thread(self._verify_failures)

    async def ci(self, number: int) -> None:
        """Wait for CI on the PR and have the agent fix a failed run (S20, S23)."""
        minutes = self._context.company.settings.ci_wait_minutes
        deadline = time.monotonic() + minutes * self.SECONDS_PER_MINUTE
        started = time.monotonic()
        while True:
            state = await asyncio.to_thread(self._context.sources.github().ci_state, self._context.item.repo, number)
            if state is CiState.PASSED or (state is CiState.NONE and time.monotonic() - started > self.CI_GRACE_SECONDS):
                return
            if state is CiState.FAILED:
                await self._fix_ci(number)
            if time.monotonic() > deadline:
                raise LimitHit(f"CI did not pass within ci_wait_minutes ({minutes})")
            await asyncio.sleep(self.CI_POLL_SECONDS)

    async def reviewed(self, plan: Plan, revisions: str) -> None:
        """The self-review of the change in `revisions` against `plan`, fixed until it
        approves (S20)."""
        review = await self._steps.review(plan, await asyncio.to_thread(self._context.checkout.changes, self._context.worktree, revisions))
        while not review.approved:
            self._budget.next_loop("the self-review")
            await self._steps.fix("the self-review found problems", "\n".join(f"- {finding}" for finding in review.findings))
            await self.verified("fix the self-review")
            review = await self._steps.review(plan, await asyncio.to_thread(self._context.checkout.changes, self._context.worktree, revisions))

    def spent(self) -> str:
        return self._budget.summary()

    async def push(self) -> None:
        await asyncio.to_thread(self._context.checkout.push, self._context.worktree, self._context.branch, self._context.token)

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
            await asyncio.to_thread(TicketSide(self._context.sources).comment, self._context.item, text)
        except CliError as failure:
            text = f"{text} The ticket comment failed: {failure}."
        await self.record(WorkStatus.BLOCKED, {"pr": number, "report": text})

    async def record(self, status: WorkStatus, changes: Mapping[str, Any]) -> None:
        """Write the entry of the task with `status` and `changes`, keeping what the state
        already holds of it (the plan, the answered comments, the rounds)."""
        item = self._context.item
        known = self._context.company.state.book().entry(item.key)
        base = known or WorkEntry(key=item.key, source=item.source, repo=item.repo, title=item.title, status=status)
        entry = base.model_copy(update={**changes, "status": status, "branch": self._context.branch})
        await self._context.company.state.record(entry)
        link = f" https://github.com/{item.repo}/pull/{entry.pr}" if entry.pr else ""
        self.LOG.info("serve: %s is %s%s %s", item.ref(), status.value, link, entry.report if status is WorkStatus.BLOCKED else "")

    def commit(self, what: str) -> bool:
        return self._context.checkout.commit_all(self._context.worktree, f"{self._context.item.ref()}: {what}", self._context.identity)

    async def _fix_ci(self, number: int) -> None:
        self._budget.next_loop("CI")
        failures = await asyncio.to_thread(self._context.sources.github().list_ci_failures, self._context.item.repo, number)
        details = "\n\n".join(f"{failure.job} / {failure.step} ({failure.url}):\n{failure.log_tail}" for failure in failures)
        await self._steps.fix("CI fails on the pull request", details or "(no failure log was found)")
        await self.verified("fix CI")
        await self.push()

    def _draft(self, text: str) -> int:
        """The PR of the task as a draft with the report: the open one, or a new one."""
        known = self._context.company.state.book().entry(self._context.item.key)
        if not self.commit("work so far") and not known:
            return 0
        self._context.checkout.push(self._context.worktree, self._context.branch, self._context.token)
        github = self._context.sources.github()
        if known is not None and known.pr:
            github.make_draft(self._context.item.repo, known.pr)
            github.comment(self._context.item.repo, known.pr, Marker().mark(text))
            return known.pr
        draft = PullRequestDraft(self._context.branch, self._context.base, self._context.title, text, draft=True)
        return int(github.open_pull(self._context.item.repo, draft))

    def _verify_failures(self) -> str:
        """The verify commands, run on a clean copy of the commit: what will be pushed."""
        checkout, worktree, shell = self._context.checkout, self._context.worktree, Shell()
        copy = checkout.verify_copy(worktree)
        found = [(command, shell.line(command, copy)) for command in self._context.company.settings.verify]
        checkout.drop_copy(worktree, copy)
        failures = "\n\n".join(f"`{command}` failed (exit {done.code}):\n{done.output[-self.OUTPUT_TAIL :]}" for command, done in found if not done.ok())
        return f"{failures}{self._ignored_note()}" if failures else ""

    def _ignored_note(self) -> str:
        ignored = self._context.checkout.ignored(self._context.worktree)
        return f"\n\ngit ignores these files of your folder, so they are not in the commit: {', '.join(ignored)}" if ignored else ""


__all__ = ["TaskFlow", "TicketSide"]
