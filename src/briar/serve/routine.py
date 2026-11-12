"""The `Routine` of a company in `briar serve` (spec 003): a ticket goes to the ticket
routine, a briar PR with new comments or a conflict to a review round, and a briar PR
that was merged or closed is only recorded (V3). A limit or a failure of git, GitHub or
Jira stops the task with the report of S23."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, ClassVar, Tuple

from briar.errors import CliError
from briar.serve.agent import AgentRunner
from briar.serve.flow import TaskFlow
from briar.serve.review import ReviewRun
from briar.serve.state import WorkStatus
from briar.serve.task import AgentSteps, Budget, TaskContext, TaskPrep
from briar.serve.ticket import TicketRun
from briar.serve.work import ServeCompany, WorkItem, WorkKind


class ServeRoutine:
    """Runs one task of a company."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    ENDED: ClassVar[Tuple[Tuple[WorkKind, WorkStatus], ...]] = ((WorkKind.MERGED, WorkStatus.MERGED), (WorkKind.CLOSED, WorkStatus.CLOSED))

    def __init__(self, company: ServeCompany, agents: Callable[[TaskContext], AgentRunner], prep: TaskPrep) -> None:
        self._company = company
        self._agents = agents
        self._prep = prep

    async def work_on(self, item: WorkItem) -> None:
        ended = dict(self.ENDED).get(item.kind)
        if ended is not None:
            await self._ended(item, ended)
            return
        review = item.kind is WorkKind.REVIEW
        context = await asyncio.to_thread(self._prep.review_context if review else self._prep.context, self._company, item)
        budget = Budget(self._company.settings.max_usd, self._company.settings.max_loops)
        steps = AgentSteps(self._agents(context), context, budget)
        flow = TaskFlow(context, steps, budget)
        run = ReviewRun(context, steps, flow) if review else TicketRun(context, steps, flow)
        try:
            await run.work()
        except CliError as stop:
            await flow.blocked(str(stop))

    async def _ended(self, item: WorkItem, status: WorkStatus) -> None:
        known = self._company.state.book().entry(item.key)
        if known is None:
            return
        await self._company.state.record(known.model_copy(update={"status": status}))
        self.LOG.info("serve: %s is %s https://github.com/%s/pull/%d", item.ref(), status.value, item.repo, item.pr)


__all__ = ["ServeRoutine"]
