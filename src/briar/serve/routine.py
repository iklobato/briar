"""The `Routine` of a company in `briar serve` (spec 003): a ticket goes to the ticket
routine, a briar PR with new comments or a conflict to a review round, and a briar PR
that was merged or closed is only recorded (V3). A limit or a failure of git, GitHub or
Jira stops the task with the report of S23."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, ClassVar, Tuple, Type

from briar.errors import CliError
from briar.serve.agent import AgentRunner
from briar.serve.flow import TaskFlow
from briar.serve.review import ReviewRun
from briar.serve.state import WorkStatus
from briar.serve.task import AgentSteps, Budget, TaskContext, TaskPrep
from briar.serve.ticket import TicketRun
from briar.serve.work import ServeCompany, WorkItem, WorkKind


@dataclass(frozen=True)
class TaskParts:
    """What one task runs with."""

    context: TaskContext
    steps: AgentSteps
    flow: TaskFlow


class ServeRoutine:
    """Runs one task of a company."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    ENDED: ClassVar[Tuple[Tuple[WorkKind, WorkStatus], ...]] = ((WorkKind.MERGED, WorkStatus.MERGED), (WorkKind.CLOSED, WorkStatus.CLOSED))
    # What stops a task with its report. Beyond `CliError` (git, GitHub, Jira, limits): a
    # task left `planned` by any other failure is taken again, and paid for, every poll.
    TASK_FAILURES: ClassVar[Tuple[Type[Exception], ...]] = (CliError, OSError, RuntimeError, ValueError, LookupError)

    def __init__(self, company: ServeCompany, agents: Callable[[TaskContext], AgentRunner], prep: TaskPrep) -> None:
        self._company = company
        self._agents = agents
        self._prep = prep

    async def work_on(self, item: WorkItem) -> None:
        ended = dict(self.ENDED).get(item.kind)
        if ended is not None:
            await self._ended(item, ended)
            return
        on_pull = item.kind is not WorkKind.TICKET
        context = await asyncio.to_thread(self._prep.review_context if on_pull else self._prep.context, self._company, item)
        budget = Budget(self._company.settings.max_usd, self._company.settings.max_loops)
        steps = AgentSteps(self._agents(context), context, budget)
        parts = TaskParts(context, steps, TaskFlow(context, steps, budget))
        try:
            await self._run(item, parts)
        except self.TASK_FAILURES as stop:
            await parts.flow.blocked(str(stop) or type(stop).__name__)

    async def _run(self, item: WorkItem, parts: TaskParts) -> None:
        if item.kind is WorkKind.REVIEW:
            await ReviewRun(parts.context, parts.steps, parts.flow).work()
            return
        ticket = TicketRun(parts.context, parts.steps, parts.flow)
        await (ticket.finish(item.pr) if item.kind is WorkKind.READY else ticket.work())

    async def _ended(self, item: WorkItem, status: WorkStatus) -> None:
        known = self._company.state.book().entry(item.key)
        if known is None:
            return
        await self._company.state.record(known.model_copy(update={"status": status}))
        self.LOG.info("serve: %s is %s https://github.com/%s/pull/%d", item.ref(), status.value, item.repo, item.pr)


__all__ = ["ServeRoutine"]
