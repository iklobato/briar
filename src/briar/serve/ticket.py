"""The ticket routine of `briar serve` (spec 003 PR 2; decisions S18 to S27):

plan -> comment on the ticket and move it -> build -> verify loop -> self-review loop ->
push and open the PR, ready -> comment and move -> wait for CI, fixing a failure.

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
        plan = await self._steps.plan()
        await asyncio.to_thread(self._announce, plan)
        await self._flow.record(WorkStatus.PLANNED, {"plan": plan.markdown()})
        await self._steps.build(plan)
        await self._flow.verified("build the plan")
        await self._flow.reviewed(plan, f"origin/{self._context.base}...HEAD")
        await self._flow.push()
        number = await asyncio.to_thread(self._publish, plan)
        await self._flow.record(WorkStatus.PR_OPEN, {"pr": number})
        await self._flow.ci(number)
        await self._flow.record(WorkStatus.DONE, {})

    def _announce(self, plan: Plan) -> None:
        side = TicketSide(self._context.sources)
        side.comment(self._context.item, f"briar plan for {self._context.item.ref()}:\n\n{plan.markdown()}")
        side.move(self._context.item, self._context.company.settings.status.started)

    def _publish(self, plan: Plan) -> int:
        draft = PullRequestDraft(self._context.branch, self._context.base, self._context.title, self._body(plan))
        number = int(self._context.sources.github().open_pull(self._context.item.repo, draft))
        side = TicketSide(self._context.sources)
        side.comment(self._context.item, f"briar opened https://github.com/{self._context.item.repo}/pull/{number}")
        side.move(self._context.item, self._context.company.settings.status.review)
        return number

    def _body(self, plan: Plan) -> str:
        item = self._context.item
        closes = f"\n\nCloses #{item.number()}" if item.source is WorkSource.GITHUB else f"\n\nTicket: {item.key}"
        return f"{plan.markdown()}{closes}\n\nBuilt by briar (spent {self._flow.spent()})."


__all__ = ["TicketRun"]
