"""The ticket routine of `briar serve` (spec 003 PR 2; decisions S18 to S27):

plan -> comment on the ticket and move it -> build -> verify loop -> self-review loop ->
push and open the PR as a draft -> comment and move -> wait for CI, fixing a failure ->
mark the PR ready, so reviewers are asked only for work that passed.

The agent only edits (D10, S25): commits, push, PR, comments and status are this code.
When a limit hits (S23) the work is pushed, the PR becomes a draft with a report, and the
ticket gets the report too (`TaskFlow.blocked`)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import ClassVar, List, Optional

from briar.errors import CliError
from briar.extract._provider import PullRequestDraft
from briar.serve.flow import TaskFlow, TicketSide
from briar.serve.state import WorkSource, WorkStatus
from briar.serve.task import AgentSteps, LimitHit, Plan, TaskContext


class TicketRun:
    """One ticket, from the plan to green CI."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    # How many usual reviewers a ready PR asks (decision D2).
    REVIEWERS = 2

    def __init__(self, context: TaskContext, steps: AgentSteps, flow: TaskFlow) -> None:
        self._context = context
        self._steps = steps
        self._flow = flow

    async def work(self) -> None:
        if await self._adopted():
            return
        plan = self._posted_plan() or await self._steps.plan()
        if await self._kept_quiet(plan):
            return
        await asyncio.to_thread(self._announce, plan)
        await self._flow.record(WorkStatus.PLANNED, {"plan": plan.markdown()})
        await self._steps.build(plan)
        await self._flow.verified("build the plan")
        if not await asyncio.to_thread(self._flow.changed):
            # A real run (2026-11-13): an operations ticket got an empty MR.
            raise LimitHit(f"the plan needs no change of the code in {self._context.item.repo}: the agent changed nothing (the plan above says why)")
        await self._flow.reviewed(plan, f"origin/{self._context.base}...HEAD")
        await self._flow.push()
        number = await asyncio.to_thread(self._open, plan)
        # Recorded before the ticket comment: a failed comment must not lose the PR.
        await self._flow.record(WorkStatus.PR_OPEN, {"pr": number})
        await asyncio.to_thread(self._published, number)
        await self.finish(number)

    async def finish(self, number: int) -> None:
        """Wait for CI on the draft PR, then mark it ready; also run for a PR left a draft
        when `serve` stopped during the wait."""
        await self._flow.ci(number)
        await asyncio.to_thread(self._context.sources.code_host().make_ready, self._context.item.repo, number)
        reviewers = await asyncio.to_thread(self._ask_reviewers, number)
        await self._flow.record(WorkStatus.DONE, {"ready_at": datetime.now(timezone.utc).isoformat(), "reviewers": reviewers})

    def _ask_reviewers(self, number: int) -> List[str]:
        """Decision D2: the usual reviewers of the repo are asked, so a ready PR does not wait
        for a person to pick them. A refused ask is logged; the PR stays ready."""
        item, host = self._context.item, self._context.sources.code_host()
        try:
            reviewers: List[str] = list(host.usual_reviewers(item.repo, self.REVIEWERS))
            if not reviewers:
                self.LOG.info("serve: %s: no usual reviewers found for %s; nobody was asked to review", item.ref(), item.repo)
                return []
            host.request_review(item.repo, number, reviewers)
            self.LOG.info("serve: %s: asked %s to review", item.ref(), ", ".join(reviewers))
            return reviewers
        except CliError as refused:
            self.LOG.warning("serve: %s: the review ask failed: %s", item.ref(), refused)
            return []

    async def _adopted(self) -> bool:
        """A PR of the task branch already exists (the state was lost, or a person closed it):
        record it and build nothing. An open draft may be work briar stopped: a person decides."""
        found = await asyncio.to_thread(self._context.sources.code_host().branch_pull, self._context.item.repo, self._context.branch)
        if found is None:
            return False
        status = WorkStatus.PR_OPEN if found.open else (WorkStatus.MERGED if found.merged else WorkStatus.CLOSED)
        status = WorkStatus.BLOCKED if found.open and found.draft else status
        if found.open:
            await asyncio.to_thread(self._context.checkout.adopt, self._context.worktree, self._context.branch)
        await self._flow.record(status, {"pr": found.number})
        return True

    async def _kept_quiet(self, plan: Plan) -> bool:
        """Decision D1: a plan with no code change (an operations ticket) gets no comment, no
        move and no branch; the task is blocked in the state only."""
        if plan.needs_code:
            return False
        report = f"the plan needs no code change in {self._context.item.repo}; nothing was posted on the ticket"
        await self._flow.record(WorkStatus.BLOCKED, {"plan": plan.markdown(), "report": report})
        return True

    def _posted_plan(self) -> Optional[Plan]:
        """The plan of a ticket taken again after a stop: the one already on the ticket. A real
        run (2026-11-13) asked for a new plan on restart, paid it twice and built that one."""
        known = self._context.company.state.book().entry(self._context.item.key)
        return Plan(known.plan) if known is not None and known.plan else None

    def _announce(self, plan: Plan) -> None:
        """The plan comment, once: a ticket taken again after a stop already has it."""
        known = self._context.company.state.book().entry(self._context.item.key)
        if known is not None and known.plan:
            return
        side = TicketSide(self._context.sources)
        side.comment(self._context.item, f"briar plan for {self._context.item.ref()}:\n\n{plan.markdown()}")
        side.move(self._context.item, self._context.company.settings.status.started)

    def _open(self, plan: Plan) -> int:
        draft = PullRequestDraft(self._context.branch, self._context.base, self._context.title, self._body(plan), draft=True)
        return int(self._context.sources.code_host().open_pull(self._context.item.repo, draft))

    def _published(self, number: int) -> None:
        side = TicketSide(self._context.sources)
        side.comment(self._context.item, f"briar opened {self._context.sources.style().pull_url(self._context.item.repo, number)}")
        side.move(self._context.item, self._context.company.settings.status.review)

    def _body(self, plan: Plan) -> str:
        item = self._context.item
        closes = f"\n\nCloses #{item.number()}" if item.source is WorkSource.GITHUB else f"\n\nTicket: {item.key}"
        return f"{plan.markdown()}{closes}\n\nBuilt by briar (spent {self._flow.spent()})."


__all__ = ["TicketRun"]
