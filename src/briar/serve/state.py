"""The work state of `briar serve`: one blob per company in the knowledge store,
`work:<company>` (decision S17). It holds each ticket briar took, with its repo,
branch and PR, and the review comments already answered; a restart picks up from it,
and the agent can read it as the history of the company."""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import ClassVar, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from briar.storage import KnowledgeStore


class WorkSource(str, Enum):
    JIRA = "jira"
    GITHUB = "github"


class WorkStatus(str, Enum):
    FOUND = "found"
    PLANNED = "planned"
    PR_OPEN = "pr-open"
    DONE = "done"
    BLOCKED = "blocked"
    MERGED = "merged"
    CLOSED = "closed"


class WorkEntry(BaseModel):
    """One ticket briar took."""

    key: str
    source: WorkSource
    repo: str
    title: str
    status: WorkStatus
    branch: str = ""
    pr: int = 0
    plan: str = ""
    # Why a task stopped on a limit or an error (S23).
    report: str = ""
    # The review comments briar answered (V4): `ReviewComment.key()` -> its `updated_at`
    # then; an edit after the answer makes it new again.
    answered: Dict[str, str] = Field(default_factory=dict)
    # The review rounds run on the PR (V11).
    rounds: int = 0
    # Why the selector took the ticket (spec 008, N12).
    reason: str = ""
    # The UTC day briar took the ticket, `YYYY-MM-DD` (decision D3: `max_new_per_day`).
    started: str = ""
    # When the PR was marked ready (ISO, UTC), who was asked to review it, and whether the one
    # reminder after 48 h was sent (decisions D2, E3).
    ready_at: str = ""
    reviewers: List[str] = Field(default_factory=list)
    reminded: bool = False

    # A ticket in one of these is not taken again; one found or planned when `serve`
    # stopped is taken again and picks up its worktree.
    TAKEN: ClassVar[Tuple[str, ...]] = ("pr-open", "done", "blocked", "merged", "closed")
    # The PR of a ticket in one of these is read each cycle (V3).
    WATCHED: ClassVar[Tuple[str, ...]] = ("pr-open", "done")

    def taken(self) -> bool:
        return self.status.value in self.TAKEN

    def watched(self) -> bool:
        return bool(self.pr) and self.status.value in self.WATCHED


class WorkBook(BaseModel):
    """Every ticket of one company, by key."""

    entries: Dict[str, WorkEntry] = Field(default_factory=dict)

    def taken(self, key: str) -> bool:
        return key in self.entries and self.entries[key].taken()

    def entry(self, key: str) -> Optional[WorkEntry]:
        return self.entries.get(key)

    def with_entry(self, entry: WorkEntry) -> "WorkBook":
        return WorkBook(entries={**self.entries, entry.key: entry})

    def started_on(self, day: str) -> int:
        """How many tickets briar took on `day` (decision D3)."""
        return sum(1 for entry in self.entries.values() if entry.started == day)

    def watched(self) -> List[WorkEntry]:
        """The PRs briar still reads: open, and not blocked (V3)."""
        return [entry for entry in self.entries.values() if entry.watched()]


class WorkState:
    """Reads and writes `work:<company>`. The tasks of one `serve` share one process, so
    an `asyncio.Lock` per company keeps two writes apart."""

    # ponytail: an in-process lock; a second `serve` on the same knowledge root is not supported.
    # The JSON indent of the blob, so a person or the agent can read it.
    INDENT = 2

    def __init__(self, store: KnowledgeStore, company: str, lock: asyncio.Lock) -> None:
        self._store = store
        self._company = company
        self._lock = lock

    def blob_name(self) -> str:
        return f"work:{self._company}"

    def book(self) -> WorkBook:
        saved = self._store.get(self.blob_name())
        return WorkBook.model_validate_json(saved) if saved else WorkBook()

    async def record(self, entry: WorkEntry) -> None:
        async with self._lock:
            self._store.put(self.blob_name(), self.book().with_entry(entry).model_dump_json(indent=self.INDENT), category="work")


__all__ = ["WorkBook", "WorkEntry", "WorkSource", "WorkState", "WorkStatus"]
