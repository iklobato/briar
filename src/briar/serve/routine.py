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
from briar.serve.day import DayPause
from briar.serve.flow import TaskFlow, TicketSide
from briar.serve.review import ReviewRun
from briar.serve.state import MoneyStop, WorkStatus
from briar.serve.task import AgentSteps, Budget, BudgetExhausted, TaskContext, TaskPrep
from briar.serve.ticket import TicketRun
from briar.serve.work import ServeCompany, WorkDay, WorkItem, WorkKind


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
    EXPECTED: ClassVar[Tuple[Type[Exception], ...]] = (CliError, OSError, RuntimeError, ValueError, LookupError)
    # A bug of briar or of a library (a real run, 2026-11-13: PyGithub raised TypeError, and
    # the task was paid for again every poll). Decision F2: it blocks too, with the traceback logged.
    UNEXPECTED: ClassVar[Tuple[Type[Exception], ...]] = (TypeError, AttributeError, ArithmeticError, AssertionError, ImportError)

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
        budget = await self._budget(item)
        self._told_rules(context)
        steps = AgentSteps(self._agents(context), context, budget)
        await self._guarded(item, TaskParts(context, steps, TaskFlow(context, steps, budget)))

    async def _guarded(self, item: WorkItem, parts: TaskParts) -> None:
        """Run the task. A pause of the day cap leaves it as it was (B13); a limit or a failure stops it with its report (S23)."""
        try:
            await self._run(item, parts)
        except DayPause as pause:
            await self._paused(item, parts.context, pause)
        except BudgetExhausted as stop:
            await parts.flow.blocked(str(stop), self._money_stop(item))
        except self.EXPECTED as stop:
            await parts.flow.blocked(str(stop) or type(stop).__name__)
        except self.UNEXPECTED as bug:
            self.LOG.exception("serve: task %s failed with an unexpected error", item.ref())
            await parts.flow.blocked(f"{type(bug).__name__}: {bug}")

    def _told_rules(self, context: TaskContext) -> None:
        """One line per task that has rule sets turned on: the sets in force and the ones left out, so
        a run shows what applied (decision Q4)."""
        rules = self._company.rules.for_task(context.item.repo, context.worktree)
        if rules.sets or rules.skipped:
            used, left_out = ", ".join(rules.used) or "none", ", ".join(rules.skipped) or "none"
            self.LOG.info("serve: %s: rules in force: %s; left out: %s", context.item.ref(), used, left_out)

    async def _paused(self, item: WorkItem, context: TaskContext, pause: DayPause) -> None:
        """Decision B13: the day cap pauses the task. Nothing is written on the ticket or the PR and the status stays as it
        was; the loop brings the task back when the day has room. With a PR, the uncommitted edits go (the next `sync` is a
        fast forward only and would fail on a dirty tree). The head may hold a commit that was not pushed yet; that is safe,
        because every push goes through `TaskFlow.verified` first. A ticket with no PR keeps its edits."""
        known = self._company.state.book().entry(item.key)
        if known is not None and known.pr:
            head = await asyncio.to_thread(context.checkout.head, context.worktree)
            await asyncio.to_thread(context.checkout.discard, context.worktree, head)
        text = f"{item.ref()} paused: {pause}; it goes on tomorrow (UTC) or when the cap is raised"
        if self._company.note(text):
            self.LOG.info("serve: %s: %s", self._company.name, text)

    def _money_stop(self, item: WorkItem) -> MoneyStop:
        """Decision B9: the cap the ticket stopped at, and the status it goes back to when the
        maintainer raises `max_usd` (the one it had when it ran out)."""
        known = self._company.state.book().entry(item.key)
        status = known.status if known is not None else WorkStatus.FOUND
        return MoneyStop(cap=self._company.settings.max_usd, resume_to=status.value)

    async def _budget(self, item: WorkItem) -> Budget:
        """Decisions B2, B3: what the earlier runs of the ticket spent counts against `max_usd`;
        the pick of the selector is booked here, once, when the ticket is first taken."""
        state, settings = self._company.state, self._company.settings
        if item.selection_cost:
            await state.claim_selection(item.entry(), item.selection_day or WorkDay().today(), item.selection_cost)
        known = state.book().entry(item.key)
        return Budget(settings.max_usd, settings.max_loops, [known.spent_total() if known is not None else 0.0])

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
        self.LOG.info("serve: %s is %s %s", item.ref(), status.value, self._prep.sources().style().pull_url(item.repo, item.pr))
        await asyncio.to_thread(self._board_after, item, status)

    def _board_after(self, item: WorkItem, status: WorkStatus) -> None:
        """Decisions E1, E2: a merged PR moves the ticket to `status.done`; a PR a person closed
        moves it back to `status.started` with a comment, and briar never takes it again. A
        refused move (no such transition) is logged, never a failure of the cycle."""
        names = self._company.settings.status
        side = TicketSide(self._prep.sources())
        target = names.done if status is WorkStatus.MERGED else names.started
        try:
            if status is WorkStatus.CLOSED:
                url = self._prep.sources().style().pull_url(item.repo, item.pr)
                side.comment(item, f"briar: {url} was closed without a merge; briar will not take this ticket again.")
            side.move(item, target)
        except CliError as refused:
            self.LOG.warning("serve: %s: could not move the ticket to %s: %s", item.ref(), target, refused)

__all__ = ["ServeRoutine"]
