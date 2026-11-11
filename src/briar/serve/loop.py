"""The loop of `briar serve` (spec 003 PR 1): one asyncio process (S15). Each company
polls every `poll_minutes` (S10, S11) and runs up to `max_parallel` tasks at once (S16),
never two on the same ticket; the scheduled extracts of the runbooks run beside it (S28).

Each task runs `ServeRoutine`: the ticket routine (`briar.serve.ticket`) or a review
round (`briar.serve.review`)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import ClassVar, List, Protocol, Set

from briar.iac.runbook.scheduler import RunbookScheduler
from briar.serve.agent import AgentRunner, BashGuard, ClaudeAgent
from briar.serve.git import Shell
from briar.serve.task import ServeCache, TaskContext, TaskPrep
from briar.serve.work import ServeCompany, WorkFinder, WorkItem, WorkSources


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


class CompanyWatch:
    """The polling of one company."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    SECONDS_PER_MINUTE = 60

    def __init__(self, company: ServeCompany, slots: TaskSlots, routine: Routine) -> None:
        self._company = company
        self._slots = slots
        self._routine = routine

    async def cycle(self) -> int:
        """One poll: find the work (in a thread: the clients are blocking) and dispatch it."""
        items: List[WorkItem] = await asyncio.to_thread(WorkFinder(self._company, WorkSources(self._company.logins)).find)
        started = sum(self._slots.dispatch(item, self._routine) for item in items)
        self.LOG.info("serve: %s: %d found, %d started", self._company.name, len(items), started)
        return started

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
        return ClaudeAgent(self._company.settings.model, guard, self._company.settings.allowed_domains)


class ServeLoops:
    """The loop of the companies of a folder, wired with their slots and routines."""

    def __init__(self, companies: List[ServeCompany], scheduler: RunbookScheduler) -> None:
        self._companies = companies
        self._scheduler = scheduler

    def loop(self) -> ServeLoop:
        watches = [CompanyWatch(company, self._slots(company), self._routine(company)) for company in self._companies]
        return ServeLoop(watches, self._scheduler)

    def _routine(self, company: ServeCompany) -> Routine:
        from briar.serve.routine import ServeRoutine

        prep = TaskPrep(WorkSources(company.logins), Shell(), ServeCache(os.environ).root())
        return ServeRoutine(company, AgentMaker(company).agent, prep)

    def _slots(self, company: ServeCompany) -> TaskSlots:
        return TaskSlots(asyncio.Semaphore(company.settings.max_parallel), set(), set())


__all__ = ["AgentMaker", "CompanyWatch", "Routine", "ServeLoop", "ServeLoops", "TaskSlots"]
