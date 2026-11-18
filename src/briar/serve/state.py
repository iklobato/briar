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


class MoneyStop(BaseModel):
    """Why a ticket is blocked for money (decisions B9, B10): the `max_usd` it stopped at, the
    status it goes back to when the maintainer raises that cap, and whether briar made its PR a
    draft (so briar, and nobody else, marks it ready again)."""

    cap: float
    resume_to: str
    drafted: bool = False


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
    # What the task spent on the model per UTC day, US$ (the daily report and `max_usd_per_day`).
    spent_by_day: Dict[str, float] = Field(default_factory=dict)
    # Set when the task stopped because the ticket spent its `max_usd`; the one stop a person lifts
    # by raising `max_usd` and restarting `serve` (decision B10).
    money_stop: Optional[MoneyStop] = None
    # Set when the day cap paused the CI wait of a review round after its replies were posted (decision B13,
    # PR B): nothing else brings that round back, so the finder emits it again until it is done.
    ci_paused: bool = False

    # A ticket in one of these is not taken again; one found or planned when `serve`
    # stopped is taken again and picks up its worktree.
    TAKEN: ClassVar[Tuple[str, ...]] = ("pr-open", "done", "blocked", "merged", "closed")
    # The PR of a ticket in one of these is read each cycle (V3).
    WATCHED: ClassVar[Tuple[str, ...]] = ("pr-open", "done")

    def taken(self) -> bool:
        return self.status.value in self.TAKEN

    def watched(self) -> bool:
        return bool(self.pr) and self.status.value in self.WATCHED

    def spent_total(self) -> float:
        """What the ticket has spent on the model over every run of it (decisions B2, B3)."""
        return sum(self.spent_by_day.values())


class WorkBook(BaseModel):
    """Every ticket of one company, by key."""

    entries: Dict[str, WorkEntry] = Field(default_factory=dict)
    # What the selector spent on each UTC day on calls that did not become a ticket (yet): a failed call, an empty
    # pick, a pick whose task has not started (decision B11, PR B). The day cap counts it.
    loose_by_day: Dict[str, float] = Field(default_factory=dict)

    def taken(self, key: str) -> bool:
        return key in self.entries and self.entries[key].taken()

    def entry(self, key: str) -> Optional[WorkEntry]:
        return self.entries.get(key)

    def with_entry(self, entry: WorkEntry) -> "WorkBook":
        return self.model_copy(update={"entries": {**self.entries, entry.key: entry}})

    def started_on(self, day: str) -> int:
        """How many tickets briar took on `day` (decision D3)."""
        return sum(1 for entry in self.entries.values() if entry.started == day)

    def spent_on(self, day: str) -> float:
        """What every task of the company and the selector spent on the model on `day`, US$ (decisions R9, B11)."""
        return sum(entry.spent_by_day.get(day, 0.0) for entry in self.entries.values()) + self.loose_by_day.get(day, 0.0)

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
            self._save(self.book().with_entry(entry))

    async def add_spend(self, fresh: WorkEntry, day: str, cost: float) -> None:
        """Book `cost` on `day` for the ticket of `fresh`, in the entry the state holds or, when
        it holds none yet, in `fresh` (decision B5: saved after every agent call)."""
        async with self._lock:
            book = self.book()
            known = book.entry(fresh.key) or fresh
            spent = {**known.spent_by_day, day: known.spent_by_day.get(day, 0.0) + cost}
            self._save(book.with_entry(known.model_copy(update={"spent_by_day": spent})))

    async def add_loose(self, day: str, cost: float) -> None:
        """Book what the selector spent on `day`, when its call returns (decision B11): the cost counts in the day
        before anyone knows whether a ticket is taken."""
        async with self._lock:
            book = self.book()
            loose = {**book.loose_by_day, day: book.loose_by_day.get(day, 0.0) + cost}
            self._save(book.model_copy(update={"loose_by_day": loose}))

    async def claim_selection(self, fresh: WorkEntry, day: str, cost: float) -> None:
        """A pick became a task: its `cost` moves from the loose spend of `day` to the entry (decision B2: the pick
        counts in the ticket total). One lock hold, so the day total does not change and nothing is counted twice."""
        async with self._lock:
            book = self.book()
            known = book.entry(fresh.key) or fresh
            spent = {**known.spent_by_day, day: known.spent_by_day.get(day, 0.0) + cost}
            loose = {**book.loose_by_day, day: max(book.loose_by_day.get(day, 0.0) - cost, 0.0)}
            moved = book.with_entry(known.model_copy(update={"spent_by_day": spent}))
            self._save(moved.model_copy(update={"loose_by_day": loose}))

    def _save(self, book: WorkBook) -> None:
        self._store.put(self.blob_name(), book.model_dump_json(indent=self.INDENT), category="work")


__all__ = ["MoneyStop", "WorkBook", "WorkEntry", "WorkSource", "WorkState", "WorkStatus"]
