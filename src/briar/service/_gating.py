"""One dry-run/execute gate, shared by every control surface.

The MCP server, `briar chat`, and the read-write dashboard all expose the
same mutating operations. Each must default to *showing what would happen*
and only act on an explicit confirmation — but the dry-run/execute decision
must live in exactly one place, not be re-derived three times.

A mutating service function takes `gate: GateMode` and returns a `GateResult`:

  * ``GateMode.DRY_RUN`` → compute and return a human ``summary`` of the
    intended side effect, perform NONE of it (``executed=False``).
  * ``GateMode.EXECUTE`` → perform the side effect, return the outcome in
    ``result`` (``executed=True``).

Front-ends translate their own confirm signal into a `GateMode`
(`GateMode.from_confirm(...)`) and render the `GateResult` their own way:
an MCP tool returns it as JSON, the dashboard renders a confirm page, chat
prompts y/N. The CLI — where the operator already typed the command — passes
``GateMode.EXECUTE`` directly, preserving today's behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class GateMode(str, Enum):
    """Whether a mutating operation previews or performs its side effect."""

    DRY_RUN = "dry_run"
    EXECUTE = "execute"

    @classmethod
    def from_confirm(cls, confirm: bool) -> "GateMode":
        """Map a front-end's boolean confirm signal onto the gate."""
        return cls.EXECUTE if confirm else cls.DRY_RUN


@dataclass
class GateResult:
    """Outcome of a gated operation.

    ``summary`` is always populated — in DRY_RUN it describes what *would*
    happen; in EXECUTE it describes what *did*. ``result`` carries the
    structured outcome only when something actually ran."""

    mode: GateMode
    executed: bool
    summary: str
    result: Optional[Dict[str, Any]] = None

    @classmethod
    def previewed(cls, summary: str) -> "GateResult":
        """A DRY_RUN result: nothing happened, here's what would."""
        return cls(mode=GateMode.DRY_RUN, executed=False, summary=summary, result=None)

    @classmethod
    def performed(cls, summary: str, result: Optional[Dict[str, Any]] = None) -> "GateResult":
        """An EXECUTE result: the side effect happened."""
        return cls(mode=GateMode.EXECUTE, executed=True, summary=summary, result=result or {})

    def as_dict(self) -> Dict[str, Any]:
        """Serialize for an MCP tool / dashboard JSON response."""
        return {
            "mode": self.mode.value,
            "executed": self.executed,
            "summary": self.summary,
            "result": self.result,
        }
