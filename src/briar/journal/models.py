"""Journal value objects.

Two domain types, both immutable once written:

- `DecisionEvent` — one recorded choice. A flat record with the choice
  name, the value selected, the alternatives considered, free-form
  rationale, and an optional `artifacts` dict (file paths, urls, ids).
- `Session` — a Composite of decisions for one command invocation. Has
  an id, a command label (e.g. `scaffold.implementation`), an optional
  target (`acme/widgets`), a start/end timestamp, and the ordered
  decision list. Renderers walk it; stores persist it; sinks publish it.

Domain-driven: these are value objects, not entities — equality is by
content, not by identity. We accept `dataclass(frozen=True)`'s mild
serialization cost in return for the invariant that a closed `Session`
cannot be mutated by a downstream consumer."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class DecisionEvent:
    """One recorded choice inside a session.

    `choice` is a dotted slug (`source.kinds.selected`,
    `archetype.resolved`) that's stable across versions — renderers
    group on it. `value` and `alternatives` are JSON-safe (list / dict
    / str / int / bool / None). `rationale` is one short human sentence
    explaining the *why*, not the *what* — the choice name already
    encodes the what."""

    choice: str
    value: Any
    rationale: str = ""
    alternatives: Tuple[Any, ...] = field(default_factory=tuple)
    artifacts: Mapping[str, str] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now_iso)
    parent_event_id: str = ""
    event_id: str = field(default_factory=_new_id)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["alternatives"] = list(self.alternatives)
        d["artifacts"] = dict(self.artifacts)
        return d


@dataclass
class Session:
    """Composite of decisions for one command invocation.

    Mutable while open (decisions append-only); call `close()` to seal.
    Sinks should refuse to publish an open session — publishing happens
    at session boundary."""

    command: str
    target: str = ""
    session_id: str = field(default_factory=_new_id)
    started_at: str = field(default_factory=_now_iso)
    ended_at: str = ""
    decisions: List[DecisionEvent] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    closed: bool = False

    def record(self, event: DecisionEvent) -> None:
        if self.closed:
            raise RuntimeError(f"session {self.session_id} is closed; cannot record {event.choice!r}")
        self.decisions.append(event)

    def close(self) -> None:
        if self.closed:
            return
        self.ended_at = _now_iso()
        self.closed = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "command": self.command,
            "target": self.target,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "decisions": [d.to_dict() for d in self.decisions],
            "metadata": dict(self.metadata),
            "closed": self.closed,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Session":
        decisions_raw = payload.get("decisions", []) or []
        decisions = [
            DecisionEvent(
                choice=d["choice"],
                value=d.get("value"),
                rationale=d.get("rationale", ""),
                alternatives=tuple(d.get("alternatives", []) or []),
                artifacts=dict(d.get("artifacts", {}) or {}),
                timestamp=d.get("timestamp", ""),
                parent_event_id=d.get("parent_event_id", ""),
                event_id=d.get("event_id", _new_id()),
            )
            for d in decisions_raw
        ]
        return cls(
            command=payload["command"],
            target=payload.get("target", ""),
            session_id=payload.get("session_id", _new_id()),
            started_at=payload.get("started_at", _now_iso()),
            ended_at=payload.get("ended_at", ""),
            decisions=decisions,
            metadata=dict(payload.get("metadata", {}) or {}),
            closed=bool(payload.get("closed", False)),
        )
