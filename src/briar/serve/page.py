"""The page of a task: the memory of a task in the knowledge (decisions W2, W3 and W4 of
`docs/decisions/2026-11-18-serve-event-workflows.md`).

Every agent call of a task is a fresh session that reads the repo again. The page is what that
session reads instead: the code writes the Facts (what happened: the plan, the commits, the
comments, the CI result, the cost) and the agent writes the Notes (why, and what is left) at the
end of a workflow. The two live under two fixed headings, so the Facts stay right even when the
Notes are wrong.

The code never decides from the page: it reads it only to know which Fact is already there, and
to give the Notes the Facts of the last workflow.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, ClassVar, List, Optional, Sequence, Set

from briar.serve.state import WorkEntry
from briar.serve.work import ServeCompany, WorkItem

if TYPE_CHECKING:
    from briar.serve.task import AgentSteps

# The headings of a page. A page with another heading is not a page.
FACTS_HEADING = "## Facts"
NOTES_HEADING = "## Notes"
HEADINGS = (FACTS_HEADING, NOTES_HEADING)
# Every line of free text after the first line of a Fact, and every line of the Notes, is written
# with this indent: a comment of a reviewer with a `## ` line or a "- `" line (a live run,
# 2026-11-21) can then never read back as a heading or as a Fact of its own.
INDENT = "  "


class PageError(ValueError):
    """The text is not a page this code wrote: an unknown heading, a heading twice, a Fact with no key."""


class PageName:
    """The file name of the page of a task, from its key. A Jira key (`ENG-1`) stays as it is;
    a GitHub key (`acme/app#7`) becomes `acme__app-7`, so two repos with the same number do not
    collide and the name is one file name."""

    def of(self, key: str) -> str:
        return key.replace("/", "__").replace("#", "-")

    def path(self, company: str, key: str) -> str:
        """The path of the page of a task, inside the knowledge folder: `<company>/tasks/<name>.md`."""
        return f"{company}/tasks/{self.of(key)}.md"


@dataclass(frozen=True)
class Fact:
    """One thing that happened in a task, written by the code. The key makes it once: the same key
    is never written twice, so a restart does not duplicate it."""

    key: str
    kind: str
    text: str
    time: str

    def line(self) -> str:
        """The Fact as page text: its first line after the key, the others indented."""
        first, *rest = self.text.splitlines() or [""]
        return "\n".join([f"- `{self.key}` {first}", *(f"{INDENT}{part}" for part in rest)])

    def more(self, line: str) -> "Fact":
        """The Fact with one more line of text (an indented line of the page)."""
        return replace(self, text=f"{self.text}\n{line}")

    @classmethod
    def of(cls, kind: str, text: str, key: str, when: str = "") -> "Fact":
        return cls(f"{kind}:{key}", kind, text, when)


class PageFile:
    """The page as text, and the strict parser of it: the headings are `## Facts` and `## Notes`,
    a repeated or unknown heading is refused."""

    def __init__(self, facts: Sequence[Fact] = (), notes: str = "") -> None:
        self._facts = list(facts)
        self._notes = notes

    def facts(self) -> List[Fact]:
        return list(self._facts)

    def notes(self) -> str:
        return self._notes

    def has(self, key: str) -> bool:
        """The Fact with this key is already there (the idempotency of the page)."""
        return any(fact.key == key for fact in self._facts)

    def add(self, fact: Fact) -> bool:
        """Add a Fact; False when its key is there already."""
        if self.has(fact.key):
            return False
        self._facts.append(fact)
        return True

    def text(self) -> str:
        lines = [FACTS_HEADING, ""]
        lines += [fact.line() for fact in self._facts]
        lines += ["", NOTES_HEADING, ""]
        lines += [f"{INDENT}{part}" if part else "" for part in self._notes.splitlines()]
        return "\n".join([*lines, ""])

    @classmethod
    def parse(cls, text: str) -> "PageFile":
        """The page in `text`. A heading that is not one of the two, or one of them twice, is an
        error: a page written by hand is not a page this code may overwrite. Free text is always
        indented (`INDENT`), so a line at the start of the line is a heading or a Fact."""
        facts: List[Fact] = []
        notes: List[str] = []
        where = ""
        seen: Set[str] = set()
        for line in text.splitlines():
            if line.startswith("## "):
                where = cls._heading(line, seen)
                continue
            if where == NOTES_HEADING:
                notes.append(line.removeprefix(INDENT))
                continue
            if where == FACTS_HEADING:
                cls._facts_line(facts, line)
        return cls(facts, "\n".join(notes).strip())

    @classmethod
    def _heading(cls, line: str, seen: Set[str]) -> str:
        if line not in HEADINGS:
            raise PageError(f"the page has an unknown heading: {line}")
        if line in seen:
            raise PageError(f"the page has the heading twice: {line}")
        seen.add(line)
        return line

    @classmethod
    def _facts_line(cls, facts: List[Fact], line: str) -> None:
        """A line under the Facts: a new Fact, or one more line of the last one (indented)."""
        if line.startswith("- `"):
            facts.append(cls._fact(line))
            return
        if line.startswith(INDENT) and facts:
            facts[-1] = facts[-1].more(line[len(INDENT) :])

    @classmethod
    def _fact(cls, line: str) -> Fact:
        """A line of a Fact: `- `<key>` <text>`."""
        end = line.find("`", 3)
        if end < 0:
            raise PageError(f"the page has a Fact without a key: {line}")
        key = line[3:end]
        text = line[end + 1 :].strip()
        return Fact(key, key.split(":", 1)[0], text, "")


class TaskPages:
    """The pages of the tasks of a company, through the knowledge store of the company. The page
    of a task is written with its path as the name of the blob (the rule of the file store: a name
    with a `/` is a literal path, and `extract/topics.py` already does this)."""

    def __init__(self, company: ServeCompany) -> None:
        self._company = company

    def path(self, key: str) -> str:
        return self._company.page_file(key)

    def read(self, key: str) -> PageFile:
        return PageFile.parse(self._text(key))

    def write(self, key: str, page: PageFile) -> None:
        self._company.knowledge().put(self.path(key), page.text())

    def set_notes(self, key: str, notes: str) -> None:
        """The Notes of the page, replaced by the new ones."""
        page = self.read(key)
        self.write(key, PageFile(page.facts(), notes))

    def exists(self, key: str) -> bool:
        return bool(self._text(key))

    def _text(self, key: str) -> str:
        """The text of the page. A page a person saved in another encoding is not a page this code wrote (a review, 2026-11-21)."""
        try:
            return self._company.knowledge().get(self.path(key))
        except UnicodeError as failure:
            raise PageError(f"the page is not UTF-8 text: {failure}") from failure


class PageBackfill:
    """The Facts of a work entry, for the page that does not exist yet: what briar knew of the
    task before the page was there."""

    def of(self, entry: Optional[WorkEntry]) -> PageFile:
        """The Facts of the entry. An entry with only a spend is a new task (the pick of the
        selector made it, a live run 2026-11-21): it has nothing to backfill."""
        if entry is None:
            return PageFile()
        facts = self._history(entry)
        if facts and entry.spent_by_day:
            facts.append(Fact.of("cost", self._spend(entry), "backfill"))
        return PageFile(facts)

    def _history(self, entry: WorkEntry) -> List[Fact]:
        """What the task did before the page was there: the plan, the PR, the rounds, the answers, the report."""
        facts: List[Fact] = []
        if entry.plan:
            facts.append(Fact.of("planned", entry.plan, "backfill"))
        if entry.pr:
            facts.append(Fact.of("pr-opened", f"PR #{entry.pr}", "backfill"))
        if entry.rounds:
            facts.append(Fact.of("rounds", f"the task had {entry.rounds} round(s)", "backfill"))
        facts += self._answered(entry)
        if entry.report:
            facts.append(Fact.of("report", entry.report, "backfill"))
        return facts

    def _answered(self, entry: WorkEntry) -> List[Fact]:
        """The review comments the task answered before the page was there."""
        return [Fact.of("answered", f"the comment {key} was answered at {when}", key) for key, when in entry.answered.items()]

    def _spend(self, entry: WorkEntry) -> str:
        total = sum(entry.spent_by_day.values())
        return f"US$ {total:.2f} in all"


class TaskFacts:
    """The observer the code calls when it knows something new about a task: `await facts.add(kind,
    text, key)`. The key makes the Fact once; the page is created when it does not exist yet, from
    what the work state already knows (the backfill)."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)

    def __init__(self, company: ServeCompany, item: WorkItem) -> None:
        self._company = company
        self._item = item

    @classmethod
    def of(cls, company: ServeCompany, item: WorkItem) -> "TaskFacts":
        """The observer of a task, for the write points that have no context of their own."""
        return cls(company, item)

    async def add(self, kind: str, text: str, key: str = "") -> None:
        """A Fact of the task. A failure of the store is a failure of the task: the caller is
        before the end of the task, where the state is not saved yet. A page a person broke by
        hand is left as it is and logged: the next review round of a ready PR must not draft it
        (a review, 2026-11-21)."""
        try:
            await asyncio.to_thread(self._add, kind, text, key)
        except PageError as failure:
            self.LOG.error("serve: the page of the task is left as it is: %s", failure)

    async def add_safe(self, kind: str, text: str, key: str = "") -> None:
        """A Fact of a task that already reached its end. A failure of the store only logs: a
        page is never worth a finished PR."""
        await self._safe(self.add(kind, text, key))

    async def notes(self, text: str) -> None:
        """The Notes of the task, written by the agent at the end of a workflow."""
        await self._safe(asyncio.to_thread(self._company.pages().set_notes, self._item.key, text))

    async def _safe(self, work: Awaitable[None]) -> None:
        """A write of the page of a task that reached its end: a failure of the store, or a page
        a person broke by hand, is logged and nothing else happens (the Facts that matter are
        re-derived from the entry)."""
        try:
            await work
        except (OSError, PageError) as failure:
            self.LOG.error("serve: the page of the task could not be written: %s", failure)

    def page(self) -> PageFile:
        """The page of the task, as it is now (an empty one when it does not exist yet)."""
        pages = self._company.pages()
        return pages.read(self._item.key) if pages.exists(self._item.key) else PageFile()

    def _add(self, kind: str, text: str, key: str) -> None:
        pages, item = self._company.pages(), self._item
        page = pages.read(item.key) if pages.exists(item.key) else PageBackfill().of(self._entry())
        page.add(Fact.of(kind, text, key, self._now()))
        pages.write(item.key, page)

    def _entry(self) -> Optional[WorkEntry]:
        return self._company.state.book().entry(self._item.key)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()


class TaskNotes:
    """The Notes of a task: one call, no tools, at the end of a workflow. The agent gets the
    previous Notes and the new Facts and writes the new Notes. A call the money cap stops is
    recorded as a Fact and the task goes on: a note is never worth a finished PR."""

    # The size of the Notes, in characters (a placeholder: no data yet).
    LIMIT = 1500

    def __init__(self, steps: "AgentSteps", facts: TaskFacts) -> None:
        self._steps = steps
        self._facts = facts

    async def write(self, key: str) -> None:
        """The Notes of the task, from the Facts it has. A `LimitHit` is a Fact, not a stop."""
        from briar.serve.task import LimitHit

        try:
            notes = await self._steps.notes(self._text())
        except LimitHit as stop:
            await self._facts.add_safe("notes-skipped", str(stop), key)
            return
        except (OSError, PageError) as failure:
            # The call comes after the end of the task: a page a person broke, or a store that fails to book the spend of the
            # call, never drafts a ready PR.
            TaskFacts.LOG.error("serve: the notes of the task could not be written: %s", failure)
            return
        await self._facts.notes(notes[: self.LIMIT])

    def _text(self) -> str:
        """The previous Notes and the Facts of the task, as the prompt wants them."""
        page = self._facts.page()
        facts = "\n".join(fact.line() for fact in page.facts())
        return f"{page.notes()}\n\n{facts}"


__all__ = [
    "Fact",
    "PageBackfill",
    "PageError",
    "PageFile",
    "PageName",
    "TaskFacts",
    "TaskNotes",
    "TaskPages",
]
