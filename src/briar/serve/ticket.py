"""The ticket routine of `briar serve` (spec 003 PR 2; decisions S18 to S27):

plan -> comment on the ticket and move it -> build -> verify loop -> self-review loop ->
push and open the PR as a draft -> comment and move -> wait for CI, fixing a failure ->
mark the PR ready, so reviewers are asked only for work that passed.

The agent only edits (D10, S25): commits, push, PR, comments and status are this code.
When a limit hits (S23) the work is pushed, the PR becomes a draft with a report, and the
ticket gets the report too (`TaskFlow.blocked`)."""

from __future__ import annotations

import asyncio

from briar.extract._provider import PullRequestDraft
from briar.serve.flow import TaskFlow, TicketSide
from briar.serve.state import WorkSource, WorkStatus
from briar.serve.task import AgentSteps, Plan, TaskContext


class TicketRun:
    """One ticket, from the plan to green CI."""

    def __init__(self, context: TaskContext, steps: AgentSteps, flow: TaskFlow) -> None:
        self._context = context
        self._steps = steps
        self._flow = flow

    async def work(self) -> None:
        if await self._adopted():
            return
        plan = await self._steps.plan()
        await asyncio.to_thread(self._announce, plan)
        await self._flow.record(WorkStatus.PLANNED, {"plan": plan.markdown()})
        await self._steps.build(plan)
        await self._flow.verified("build the plan")
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
        await self._flow.record(WorkStatus.DONE, {})

    async def _adopted(self) -> bool:
        """A PR of the task branch already exists (the state was lost, or a person closed it):
        record it and build nothing. An open draft may be work briar stopped: a person decides."""
        found = await asyncio.to_thread(self._context.sources.code_host().branch_pull, self._context.item.repo, self._context.branch)
        if found is None:
            return False
        status = WorkStatus.PR_OPEN if found.open else (WorkStatus.MERGED if found.merged else WorkStatus.CLOSED)
        status = WorkStatus.BLOCKED if found.open and found.draft else status
        await self._flow.record(status, {"pr": found.number})
        return True

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
