"""The loop of `briar serve` (spec 003 PR 1): one asyncio process (S15). Each company
polls every `poll_minutes` (S10, S11) and runs up to `max_parallel` tasks at once (S16),
never two on the same ticket; the scheduled extracts of the runbooks run beside it (S28).

Each task runs `ServeRoutine`: the ticket routine (`briar.serve.ticket`) or a review
round (`briar.serve.review`)."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, ClassVar, List, Protocol, Set, Tuple, Type

from briar.errors import CliError
from briar.iac.runbook.scheduler import RunbookScheduler
from briar.serve.agent import AgentRunner, BashGuard, ClaudeAgent, SandboxRoom
from briar.serve.chat import ChatReader
from briar.serve.git import Shell
from briar.serve.health import LoginWatch
from briar.serve.open_work import OpenWork
from briar.serve.pause import ServeStop
from briar.serve.select import Choice, Selector
from briar.serve.task import ServeCache, TaskContext, TaskPrep
from briar.serve.report import DailyReport
from briar.serve.state import WorkBook
from briar.serve.status import ServeStatus
from briar.serve.work import BaseDrift, BudgetReopen, ReviewReminders, ServeCompany, WorkDay, WorkFinder, WorkItem, WorkKind, WorkSources

if TYPE_CHECKING:
    from briar.commands._enums import ExitCode


class Routine(Protocol):
    """What runs one task."""

    async def work_on(self, item: WorkItem) -> None: ...


class TaskSlots:
    """Up to `max_parallel` tasks of one company at once (S15, S16), never two with the
    same key. A task that fails is logged; the others go on."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)

    def __init__(self, slots: asyncio.Semaphore, running: Set[str], tasks: Set["asyncio.Task[None]"]) -> None:
        self._slots = slots
        self._running = running
        self._tasks = tasks

    def dispatch(self, item: WorkItem, routine: Routine) -> bool:
        if item.key in self._running:
            return False
        self._running.add(item.key)
        task = asyncio.create_task(self._run(item, routine), name=item.key)
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return True

    def busy(self) -> int:
        return len(self._running)

    def waiting(self, items: List[WorkItem]) -> List[WorkItem]:
        """The items with no task running."""
        return [item for item in items if item.key not in self._running]

    async def drain(self) -> None:
        """Wait for every task that is running (tests, and a clean stop)."""
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _run(self, item: WorkItem, routine: Routine) -> None:
        try:
            async with self._slots:
                await routine.work_on(item)
        finally:
            self._running.discard(item.key)

    def _finished(self, task: "asyncio.Task[None]") -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.LOG.error("serve: task %s failed: %s", task.get_name(), task.exception())


@dataclass(frozen=True)
class WatchWork:
    """What a company watch runs: the routine of each task, and the choice of the tickets."""

    routine: Routine
    choice: Choice


class CompanyWatch:
    """The polling of one company: the PRs of briar are dispatched as found; the tickets
    go through the choice (spec 008), except a ticket briar already started, which goes on
    in the repo it started in."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    SECONDS_PER_MINUTE = 60
    # What needs no model call, so the day cap does not hold it (B13).
    NO_MODEL: ClassVar[Tuple[WorkKind, ...]] = (WorkKind.MERGED, WorkKind.CLOSED)
    # What one failed poll raises: an adapter error (`CliError`) or the network.
    POLL_FAILURES: ClassVar[Tuple[Type[Exception], ...]] = (CliError, OSError, RuntimeError, ValueError, LookupError)

    def __init__(self, company: ServeCompany, slots: TaskSlots, work: WatchWork) -> None:
        self._company = company
        self._slots = slots
        self._work = work

    async def cycle(self) -> int:
        """One poll: find the work (in a thread: the clients are blocking) and dispatch it.
        A failed poll (GitHub or Jira down, a state write that fails) is logged and tried again next poll: it must not
        stop the other companies and the tasks that are running. The whole poll is inside the guard: the reopen, the
        selector with its state write and the report used to sit outside it, and an `OSError` there ended `serve` (audit 7).
        Once `serve` was asked to stop (a refused login, decision P13) no company polls again."""
        started = await self._cycled()
        await asyncio.to_thread(ServeStatus(self._company).polled)
        return started

    async def _cycled(self) -> int:
        if self._company.stop.asked():
            return 0
        try:
            return await self._poll()
        except self.POLL_FAILURES as failure:
            self.LOG.error("serve: %s: the poll failed, trying again next poll: %s", self._company.name, failure)
            await self._confirmed()
            return 0

    async def _confirmed(self) -> None:
        """A failed poll may be a dead login (WP1, decision D9): ask "who am I" of the code host and Jira. Only a 401 stops `serve`;
        any other failure is tried again at the next poll, as before."""
        sources = WorkSources(self._company.logins, self._company.settings.code_host)
        dead = await asyncio.to_thread(LoginWatch(self._company, sources).dead)
        if dead is not None and self._company.stop.request(dead):
            self.LOG.error("serve: %s: %s", self._company.name, dead.line())

    async def _poll(self) -> int:
        sources = WorkSources(self._company.logins, self._company.settings.code_host)
        drift = BaseDrift(self._company, sources, ServeCache(os.environ).root())
        await BudgetReopen(self._company).apply()
        items: List[WorkItem] = await asyncio.to_thread(WorkFinder(self._company, sources, drift).find)
        await ReviewReminders(self._company, sources).send()
        capped = self._capped()
        pulls = [item for item in items if item.kind is not WorkKind.TICKET and (item.kind in self.NO_MODEL or not capped)]
        started = sum(self._slots.dispatch(item, self._work.routine) for item in pulls) + (0 if capped else await self._tickets(items))
        self.LOG.info("serve: %s: %d found, %d started", self._company.name, len(items), started)
        await asyncio.to_thread(DailyReport(self._company, sources).write)
        return started

    async def _tickets(self, items: List[WorkItem]) -> int:
        book = self._company.state.book()
        tickets = self._slots.waiting([item for item in items if item.kind is WorkKind.TICKET])
        resumed = [item.resumed(entry) for item in tickets if (entry := book.entry(item.key)) is not None]
        fresh = [item for item in tickets if book.entry(item.key) is None]
        free = min(max(self._company.settings.max_parallel - self._slots.busy() - len(resumed), 0), self._left_today(book, fresh))
        picked = await self._work.choice.choose(fresh, free)
        for item in picked:
            self.LOG.info("serve: %s: picked %s for %s: %s", self._company.name, item.key, item.repo, item.reason)
        return sum(self._slots.dispatch(item, self._work.routine) for item in [*resumed, *picked])

    def _capped(self) -> bool:
        """Decisions B11 and B13 (PR B): with the spend booked today at `max_usd_per_day`, nothing that needs the model is
        dispatched (a resumed ticket, a review round, a ready PR, the selector): it would only fetch a clone and build a
        worktree for a task that pauses at its first call. A merged or closed PR needs the state only, so it still goes.
        Calls in flight are not counted here: they hold their share and will be booked."""
        if not self._company.capped():
            return False
        cap = self._company.settings.max_usd_per_day
        self._noted(f"the daily cap max_usd_per_day of US$ {cap:g} is reached: nothing that needs the model runs until tomorrow (UTC)")
        return True

    def _left_today(self, book: WorkBook, fresh: List[WorkItem]) -> int:
        """Decision D3: the new tickets the company may still start this UTC day, by `max_new_per_day`.
        The money cap is the gate of `cycle` (B11, B13)."""
        return min(len(fresh), self._by_count(book, fresh))

    def _by_count(self, book: WorkBook, fresh: List[WorkItem]) -> int:
        limit = self._company.settings.max_new_per_day
        if not limit:
            return len(fresh)
        started = book.started_on(WorkDay().today())
        if started >= limit and fresh:
            self._noted(f"{started} new ticket(s) started today (max_new_per_day {limit}): no new ticket until tomorrow (UTC)")
        return max(limit - started, 0)

    def _noted(self, note: str) -> None:
        """One note for each day: a poll repeats the same note while the cap holds, in the log and in the report."""
        if self._company.note(note):
            self.LOG.info("serve: %s: %s", self._company.name, note)

    async def watch(self) -> None:
        while True:
            await self.cycle()
            await asyncio.sleep(self._company.settings.poll_minutes * self.SECONDS_PER_MINUTE)

    async def settle(self) -> None:
        """The stop of `serve` (decision P31): the tasks that run get `call_minutes` to finish. What is left after that is
        cancelled (the timeout cancels the drain, and the drain its tasks); the next start brings it back to the origin (#136)."""
        dead = self._company.stop.reason()
        status = ServeStatus(self._company)
        if dead is not None:
            await asyncio.to_thread(status.stopping, dead)
        try:
            await asyncio.wait_for(self._slots.drain(), self._company.settings.call_seconds())
        except asyncio.TimeoutError:
            self.LOG.warning("serve: %s: tasks that did not finish within call_minutes were cancelled", self._company.name)
        if dead is not None:
            await asyncio.to_thread(status.stopped, dead)


class ServeLoop:
    """Every company watch and the scheduled extracts, in one event loop."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    # Seconds between two looks at the extract schedule (C14: every second).
    TICK_SECONDS = 1.0

    def __init__(self, watches: List[CompanyWatch], scheduler: RunbookScheduler, stop: ServeStop) -> None:
        self._watches = watches
        self._scheduler = scheduler
        self._stop = stop

    def serve_forever(self) -> None:
        asyncio.run(self.serve_until_stopped())

    def stop(self) -> ServeStop:
        return self._stop

    def exit_code(self) -> "ExitCode":
        """3 when `serve` stopped because a login was refused (decision P13), else 0."""
        from briar.commands._enums import ExitCode

        return ExitCode.LOGIN_REFUSED if self._stop.asked() else ExitCode.OK

    async def serve_until_stopped(self) -> None:
        """Every watch and the extracts, until the process is stopped or a login is refused. A task of the loops that ends
        with an error ends `serve`, as `gather` did. On a refused login the loops stop, the running tasks get their time, and
        this returns: `exit_code` says why."""
        loops = [asyncio.create_task(self._extracts()), *(asyncio.create_task(watch.watch()) for watch in self._watches)]
        asked = asyncio.create_task(self._stop.wait())
        try:
            await asyncio.wait([asked, *loops], return_when=asyncio.FIRST_COMPLETED)
            self._crashed(loops)
        finally:
            for task in [asked, *loops]:
                task.cancel()
            await asyncio.gather(asked, *loops, return_exceptions=True)
        if self._stop.asked():
            await self._settled()

    def _crashed(self, loops: List["asyncio.Task[None]"]) -> None:
        """A loop that ended by itself ended with an error: raise it."""
        for task in loops:
            if task.done():
                task.result()

    async def _settled(self) -> None:
        dead = self._stop.reason()
        if dead is not None:
            self.LOG.error("serve: stopping: %s", dead.line())
        await asyncio.gather(*(watch.settle() for watch in self._watches))

    async def _extracts(self) -> None:
        while True:
            await asyncio.to_thread(self._scheduler.tick)
            await asyncio.sleep(self.TICK_SECONDS)


class AgentMaker:
    """The agent of a task: the Claude Agent SDK with the model, the bash allowlist and the
    allowed domains of the company, kept inside the worktree of the task; it reads the
    knowledge folder too (S1, S5, S25, V12, V13)."""

    def __init__(self, company: ServeCompany) -> None:
        self._company = company

    def agent(self, context: TaskContext) -> AgentRunner:
        guard = BashGuard(context.worktree, self._company.settings.bash, [self._company.root])
        room = SandboxRoom(tuple(self._company.settings.allowed_domains), ServeCache(os.environ).root() / "tools")
        return ClaudeAgent(self._company.settings.model, guard, room)


class ServeLoops:
    """The loop of the companies of a folder, wired with their slots and routines."""

    def __init__(self, companies: List[ServeCompany], scheduler: RunbookScheduler) -> None:
        self._companies = companies
        self._scheduler = scheduler

    def loop(self) -> ServeLoop:
        watches = [CompanyWatch(company, self._slots(company), WatchWork(self._routine(company), self._choice(company))) for company in self._companies]
        return ServeLoop(watches, self._scheduler, self._companies[0].stop if self._companies else ServeStop.make())

    def _choice(self, company: ServeCompany) -> Choice:
        """The selector call only reads (`Access.READ`), inside the knowledge folder."""
        guard = BashGuard(company.root, company.settings.bash, [company.root])
        room = SandboxRoom((), ServeCache(os.environ).root() / "tools")
        selector = Selector(ClaudeAgent(company.settings.model, guard, room), company)
        chat = ChatReader(WorkSources(company.logins).slack(), company.settings, date.today) if company.settings.chat.slack else None
        open_work = OpenWork(WorkSources(company.logins, company.settings.code_host), company.settings)
        return Choice(selector, company, date.today, chat, open_work=open_work)

    def _routine(self, company: ServeCompany) -> Routine:
        from briar.serve.routine import ServeRoutine

        prep = TaskPrep(WorkSources(company.logins, company.settings.code_host), Shell(), ServeCache(os.environ).root())
        return ServeRoutine(company, AgentMaker(company).agent, prep)

    def _slots(self, company: ServeCompany) -> TaskSlots:
        return TaskSlots(asyncio.Semaphore(company.settings.max_parallel), set(), set())


__all__ = ["AgentMaker", "CompanyWatch", "Routine", "ServeLoop", "ServeLoops", "TaskSlots", "WatchWork"]
