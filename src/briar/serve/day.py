"""The hard daily cap of `briar serve` (decisions B11 to B14 of docs/decisions/2026-11-18-serve-spend-caps.md,
PR B): each agent call gets the smaller of what its ticket and the day have left, minus what the other calls
in flight hold. A call with no room waits while the BOOKED spend is under the cap and pauses only when the
booked spend reaches it."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, List


class DayPause(Exception):
    """The spend booked today reached `max_usd_per_day` (decision B13): the task pauses, it is not blocked.
    Not a `CliError` and not a `BudgetExhausted`: the routine blocks the task on both."""


@dataclass(frozen=True)
class Grant:
    """What one agent call may spend: `amount` is its `max_budget_usd`, and `held` is what the day keeps
    reserved for it until the call is booked (0 when the cap is off)."""

    amount: float
    held: float


class DayCap:
    """The reservations of the calls in flight of one company, in memory (decision B12). A task that waits for
    room holds nothing, so reservations only belong to calls that end, and a wait cannot dead lock."""

    def __init__(self) -> None:
        self._reserved = 0.0
        self._waiting: List[asyncio.Future[None]] = []

    async def grant(self, cap: float, booked: Callable[[], float], ticket_left: float) -> Grant:
        """`cap` is `max_usd_per_day` (0 is off), `booked` reads the spend booked today, `ticket_left` is what the
        ticket may still spend. The check and the reservation have no `await` between them, so two tasks never
        take the same room."""
        if cap <= 0:
            return Grant(ticket_left, 0.0)
        while True:
            left = cap - booked()
            if left <= 0:
                raise DayPause(f"the daily cap max_usd_per_day of US$ {cap:g} is reached")
            free = left - self._reserved
            if free > 0:
                amount = min(ticket_left, free)
                self._reserved += amount
                return Grant(amount, amount)
            await self._released()

    def release(self, grant: Grant) -> None:
        """Give back what the call held. Call it AFTER the cost is booked, so booked plus reserved never under counts."""
        self._reserved = max(self._reserved - grant.held, 0.0)
        for future in list(self._waiting):
            if not future.done():
                future.set_result(None)

    async def _released(self) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiting.append(future)
        try:
            await future
        finally:
            self._waiting.remove(future)


__all__ = ["DayCap", "DayPause", "Grant"]
