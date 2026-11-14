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
from typing import ClassVar, List, Protocol, Set, Tuple, Type

from briar.errors import CliError
from briar.iac.runbook.scheduler import RunbookScheduler
from briar.serve.agent import AgentRunner, BashGuard, ClaudeAgent, SandboxRoom
from briar.serve.chat import ChatReader
from briar.serve.git import Shell
from briar.serve.open_work import OpenWork
from briar.serve.select import Choice, Selector
from briar.serve.task import ServeCache, TaskContext, TaskPrep
from briar.serve.report import DailyReport
from briar.serve.state import WorkBook
from briar.serve.work import BaseDrift, ReviewReminders, ServeCompany, WorkDay, WorkFinder, WorkItem, WorkKind, WorkSources


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
    # What one failed poll raises: an adapter error (`CliError`) or the network.
    POLL_FAILURES: ClassVar[Tuple[Type[Exception], ...]] = (CliError, OSError, RuntimeError, ValueError, LookupError)

    def __init__(self, company: ServeCompany, slots: TaskSlots, work: WatchWork) -> None:
        self._company = company
        self._slots = slots
        self._work = work

    async def cycle(self) -> int:
        """One poll: find the work (in a thread: the clients are blocking) and dispatch it.
        A failed poll (GitHub or Jira down) is logged and tried again next poll: it must not
        stop the other companies and the tasks that are running."""
        sources = WorkSources(self._company.logins, self._company.settings.code_host)
        drift = BaseDrift(self._company, sources, ServeCache(os.environ).root())
        try:
            items: List[WorkItem] = await asyncio.to_thread(WorkFinder(self._company, sources, drift).find)
        except self.POLL_FAILURES as failure:
            self.LOG.error("serve: %s: the poll failed, trying again next poll: %s", self._company.name, failure)
            return 0
        await ReviewReminders(self._company, sources).send()
        pulls = [item for item in items if item.kind is not WorkKind.TICKET]
        started = sum(self._slots.dispatch(item, self._work.routine) for item in pulls) + await self._tickets(items)
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

    def _left_today(self, book: WorkBook, fresh: List[WorkItem]) -> int:
        """Decision D3: the new tickets the company may still start this UTC day."""
        limit = self._company.settings.max_new_per_day
        if not limit:
            return len(fresh)
        started = book.started_on(WorkDay().today())
        if started >= limit and fresh:
            note = f"{started} new ticket(s) started today (max_new_per_day {limit}): no new ticket until tomorrow (UTC)"
            self.LOG.info("serve: %s: %s", self._company.name, note)
            self._company.notes.setdefault(WorkDay().today(), []).append(note)
        return max(limit - started, 0)

    async def watch(self) -> None:
        while True:
            await self.cycle()
            await asyncio.sleep(self._company.settings.poll_minutes * self.SECONDS_PER_MINUTE)


class ServeLoop:
    """Every company watch and the scheduled extracts, in one event loop."""

    # Seconds between two looks at the extract schedule (C14: every second).
    TICK_SECONDS = 1.0

    def __init__(self, watches: List[CompanyWatch], scheduler: RunbookScheduler) -> None:
        self._watches = watches
        self._scheduler = scheduler

    def serve_forever(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        await asyncio.gather(self._extracts(), *(watch.watch() for watch in self._watches))

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
        return ServeLoop(watches, self._scheduler)

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
