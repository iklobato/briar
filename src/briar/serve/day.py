"""The hard daily cap of `briar serve` (decisions B11 to B14 and B19 to B23 of
docs/decisions/2026-11-18-serve-spend-caps.md, PR B): each agent call gets the smaller of what its ticket and
the day have left, minus what the other calls in flight hold. A call whose whole ticket does not fit waits while
other calls hold the day; it is cut to the day left only when no other call is in flight, and the task pauses only
when the BOOKED spend reaches the cap."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, List, Optional


class DayPause(Exception):
    """The spend booked today reached `max_usd_per_day` (decision B13): the task pauses, it is not blocked.
    Not a `CliError` and not a `BudgetExhausted`: the routine blocks the task on both."""


@dataclass(frozen=True)
class Grant:
    """What one agent call may spend: `amount` is its `max_budget_usd`, and `held` is what the day keeps
    reserved for it until the call is booked (0 when the cap is off). `day` is the UTC day it was granted on, set by
    `ServeCompany`: a call that crosses midnight closes the day it was granted on, not the one it returns on."""

    amount: float
    held: float
    day: str = ""


class DayCap:
    """The calls in flight of one company, in memory (decision B12): what they hold, and how many they are. A task
    that waits for room holds nothing, so reservations only belong to calls that end and a wait cannot dead lock."""

    def __init__(self) -> None:
        self._reserved = 0.0
        # Counted apart from `_reserved`: float residue must never make a call in flight look like none, or a task
        # would wait with nobody left to wake it.
        self._in_flight = 0
        self._exhausted = ""
        self._waiting: List[asyncio.Future[None]] = []

    def offer(self, cap: float, booked: Callable[[], float], ticket_left: float) -> Optional[Grant]:
        """The grant for a call right now, or None when other calls hold the room it needs (decision B19). `cap` is
        `max_usd_per_day` (0 is off), `booked` reads the spend booked today, `ticket_left` is what the ticket may still
        spend. It never waits, and it has no `await`, so two tasks never take the same room.
        A call gets its whole `ticket_left` when it fits. It gets less (the day left) only when no other call is in
        flight, so the day itself is the limit; with calls in flight a cut grant would stop the call early while the
        booked spend is still far under the cap."""
        if cap <= 0:
            return Grant(ticket_left, 0.0)
        left = cap - booked()
        if left <= 0:
            raise DayPause(f"the daily cap max_usd_per_day of US$ {cap:g} is reached")
        if left - self._reserved >= ticket_left:
            return self._take(ticket_left)
        return None if self._in_flight else self._take(left)

    async def grant(self, cap: float, booked: Callable[[], float], ticket_left: float) -> Grant:
        """Like `offer`, but waits for a call in flight to end when the room is held (decision B19)."""
        while True:
            offered = self.offer(cap, booked, ticket_left)
            if offered is not None:
                return offered
            await self._released()

    def release(self, grant: Grant) -> None:
        """Give back what the call held. Call it AFTER the cost is booked, so booked plus reserved never under counts."""
        if grant.held <= 0:
            return
        self._in_flight -= 1
        self._reserved = max(self._reserved - grant.held, 0.0) if self._in_flight else 0.0
        for future in list(self._waiting):
            if not future.done():
                future.set_result(None)

    def exhaust(self, day: str) -> None:
        """A call was stopped at the day grant on `day`: the day is spent, even when the SDK stopped a hair under the cap."""
        self._exhausted = day

    def exhausted(self, day: str) -> bool:
        return self._exhausted == day

    def _take(self, amount: float) -> Grant:
        self._reserved += amount
        self._in_flight += 1
        return Grant(amount, amount)

    async def _released(self) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiting.append(future)
        try:
            await future
        finally:
            self._waiting.remove(future)


__all__ = ["DayCap", "DayPause", "Grant"]
