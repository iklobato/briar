"""What a refused model call asks of `briar serve` (WP1 of the production plan, decisions P13, P21, P30 to P32 of the private plan).

A call the model refuses is not the fault of the ticket: the day cap already pauses a task and writes nothing on it (B13), and a
refused login or a plan limit do the same. `ModelPause` is the family: the day cap, the plan limit and a refused login. A refused
login also asks `serve` to stop (`ServeStop`), because every other task would meet the same wall; a plan limit closes the
`UsageGate`, so the loop sends one probe task per poll until a call works again."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Optional

from briar.serve.agent import AgentAnswer, Refusal

if TYPE_CHECKING:
    from briar.serve.work import ServeCompany


class ModelPause(Exception):
    """A pause that is not the fault of the task: it is not blocked, nothing is written on its ticket or PR, its status stays, and
    what was never verified goes back to what the origin has. Not a `CliError`: the routine blocks the task on those."""

    # What happens to the task next, for the note of the daily report.
    RESUME: ClassVar[str] = ""

    def note(self, ref: str) -> str:
        return f"{ref} paused: {self}; {self.RESUME}"

    def settle(self, company: "ServeCompany") -> None:
        """What the pause asks of `serve` besides pausing the task: the day cap asks nothing."""
        return None


@dataclass(frozen=True)
class DeadLogin:
    """A login that was refused: its source (`model`, `github`, `gitlab` or `jira`), the company, what the source said, and the fix."""

    source: str
    company: str
    detail: str
    fix: str

    def subject(self) -> str:
        return "the model" if self.source == "model" else self.source

    def line(self) -> str:
        return f"the login of {self.subject()} ({self.company}) was refused: {self.detail}; {self.fix}"


class LoginRefused(ModelPause):
    """A login was refused: `serve` stops, because every other task would meet the same wall (decision P13)."""

    RESUME: ClassVar[str] = "serve stops now: renew the login and start it again"
    dead: DeadLogin

    @classmethod
    def of(cls, dead: DeadLogin) -> "LoginRefused":
        refused = cls(f"the login of {dead.subject()} was refused: {dead.detail}")
        refused.dead = dead
        return refused

    def settle(self, company: "ServeCompany") -> None:
        company.stop.request(self.dead)


class UsageLimitHit(ModelPause):
    """The limit of the plan is hit (decision P21): the task waits, the loop probes again at the next poll (P32)."""

    RESUME: ClassVar[str] = "it goes on when the limit is lifted"

    @classmethod
    def of(cls, reason: str) -> "UsageLimitHit":
        return cls(f"the plan limit is hit: {reason}")

    def settle(self, company: "ServeCompany") -> None:
        company.gate.hit(str(self))


class Refused:
    """The pause a refused answer asks for, or None when the call was not refused."""

    FIX = "set a valid CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY, in the environment or in secrets.env"

    def __init__(self, company: str) -> None:
        self._company = company

    def pause(self, answer: AgentAnswer) -> Optional[ModelPause]:
        if answer.refusal is Refusal.LOGIN:
            return LoginRefused.of(DeadLogin("model", self._company, answer.reason, self.FIX))
        if answer.refusal is Refusal.LIMIT:
            return UsageLimitHit.of(answer.reason)
        return None


class UsageGate:
    """Closed while the plan limit is hit (decision P21): model work waits, and the loop lets one probe task through at each
    poll (decision P32). Any call that works opens it again. One for all the companies: they share one plan."""

    def __init__(self) -> None:
        self._reason = ""

    def hit(self, reason: str) -> None:
        self._reason = reason

    def clear(self) -> None:
        self._reason = ""

    def closed(self) -> bool:
        return bool(self._reason)

    def reason(self) -> str:
        return self._reason


class ServeStop:
    """The request to stop the whole `serve` for a refused login (decision P13). The first reason is kept: when two companies
    fail at once, the file of the stop names the first one."""

    def __init__(self, event: asyncio.Event) -> None:
        self._dead: Optional[DeadLogin] = None
        self._event = event

    @classmethod
    def make(cls) -> "ServeStop":
        return cls(asyncio.Event())

    def request(self, dead: DeadLogin) -> bool:
        """True when this is the first request."""
        if self._dead is not None:
            return False
        self._dead = dead
        self._event.set()
        return True

    def asked(self) -> bool:
        return self._dead is not None

    def reason(self) -> Optional[DeadLogin]:
        return self._dead

    async def wait(self) -> None:
        await self._event.wait()


__all__ = ["DeadLogin", "LoginRefused", "ModelPause", "Refused", "ServeStop", "UsageGate", "UsageLimitHit"]
