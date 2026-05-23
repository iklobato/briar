"""Data shapes for `briar plan`.

A plan is an ordered list of `PlanCard`s. Each card carries the
extracted ticket summary, in/out-of-scope notes, the upstream cards
it depends on, and the git branch metadata the engineer flow will
use when picking the card up. Status moves pending → in_progress →
done as the operator (or the implementer agent) advances through
the plan.

The shape is stable across storage backends — the JSON emitted by
`to_dict` is what `plan:<name>` blobs hold."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


PLAN_SCHEMA_VERSION = 1


@dataclass
class PlanCard:
    """One ticket-shaped unit of work, enriched with synthesis output
    and a resolved branch parent."""

    key: str
    title: str
    url: str = ""
    tracker: str = ""
    summary: str = ""
    in_scope: List[str] = field(default_factory=list)
    out_of_scope: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    branch_name: str = ""
    branch_parent: str = ""
    status: str = "pending"  # pending | in_progress | done | blocked
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "PlanCard":
        return cls(
            key=str(raw.get("key") or ""),
            title=str(raw.get("title") or ""),
            url=str(raw.get("url") or ""),
            tracker=str(raw.get("tracker") or ""),
            summary=str(raw.get("summary") or ""),
            in_scope=list(raw.get("in_scope") or []),
            out_of_scope=list(raw.get("out_of_scope") or []),
            risks=list(raw.get("risks") or []),
            sources=list(raw.get("sources") or []),
            depends_on=list(raw.get("depends_on") or []),
            branch_name=str(raw.get("branch_name") or ""),
            branch_parent=str(raw.get("branch_parent") or ""),
            status=str(raw.get("status") or "pending"),
            notes=str(raw.get("notes") or ""),
        )


@dataclass
class ImplementationPlan:
    """Top-level plan record. The ordered `cards` list is the result
    of dependency-graph topological sort; consumers must not re-order
    it on read."""

    name: str
    board_url: str
    tracker: str
    project: str
    cascade: bool = False
    company: str = ""
    owner: str = ""
    repo: str = ""
    default_branch: str = "main"
    created_at: str = ""
    cards: List[PlanCard] = field(default_factory=list)
    version: int = PLAN_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "board_url": self.board_url,
            "tracker": self.tracker,
            "project": self.project,
            "cascade": self.cascade,
            "company": self.company,
            "owner": self.owner,
            "repo": self.repo,
            "default_branch": self.default_branch,
            "created_at": self.created_at,
            "cards": [c.to_dict() for c in self.cards],
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ImplementationPlan":
        return cls(
            name=str(raw.get("name") or ""),
            board_url=str(raw.get("board_url") or ""),
            tracker=str(raw.get("tracker") or ""),
            project=str(raw.get("project") or ""),
            cascade=bool(raw.get("cascade") or False),
            company=str(raw.get("company") or ""),
            owner=str(raw.get("owner") or ""),
            repo=str(raw.get("repo") or ""),
            default_branch=str(raw.get("default_branch") or "main"),
            created_at=str(raw.get("created_at") or ""),
            cards=[PlanCard.from_dict(c) for c in (raw.get("cards") or [])],
            version=int(raw.get("version") or PLAN_SCHEMA_VERSION),
        )

    def next_pending(self) -> "PlanCard | None":
        """First card whose deps are all `done`. Returns None when the
        plan is finished or fully blocked."""
        done = {c.key for c in self.cards if c.status == "done"}
        for card in self.cards:
            if card.status != "pending":
                continue
            if all(dep in done for dep in card.depends_on):
                return card
        return None
