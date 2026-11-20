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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, ClassVar, List, Optional, Sequence

from briar.serve.state import WorkEntry
from briar.serve.work import ServeCompany, WorkItem

if TYPE_CHECKING:
    from briar.serve.task import AgentSteps

# The headings of a page. A page with another heading is not a page.
FACTS_HEADING = "## Facts"
NOTES_HEADING = "## Notes"
HEADINGS = (FACTS_HEADING, NOTES_HEADING)


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
        return f"- `{self.key}` {self.text}"

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
        lines += ["", NOTES_HEADING, "", self._notes, ""]
        return "\n".join(lines)

    @classmethod
    def parse(cls, text: str) -> "PageFile":
        """The page in `text`. A heading that is not one of the two, or one of them twice, is an
        error: a page written by hand is not a page this code may overwrite."""
        facts: List[Fact] = []
        notes = ""
        where = ""
        seen = set()
        for line in text.splitlines():
            if line.startswith("## "):
                if line not in HEADINGS:
                    raise ValueError(f"the page has an unknown heading: {line}")
                if line in seen:
                    raise ValueError(f"the page has the heading twice: {line}")
                seen.add(line)
                where = line
                continue
            if where == NOTES_HEADING:
                notes += f"{line}\n"
                continue
            if where == FACTS_HEADING and line.startswith("- `"):
                facts.append(cls._fact(line))
        return cls(facts, notes.strip())

    @classmethod
    def _fact(cls, line: str) -> Fact:
        """A line of a Fact: `- `<key>` <text>`."""
        end = line.find("`", 3)
        if end < 0:
            raise ValueError(f"the page has a Fact without a key: {line}")
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
        return PageFile.parse(self._company.knowledge().get(self.path(key)))

    def write(self, key: str, page: PageFile) -> None:
        self._company.knowledge().put(self.path(key), page.text())

    def set_notes(self, key: str, notes: str) -> None:
        """The Notes of the page, replaced by the new ones."""
        page = self.read(key)
        self.write(key, PageFile(page.facts(), notes))

    def exists(self, key: str) -> bool:
        return bool(self._company.knowledge().get(self.path(key)))


class PageBackfill:
    """The Facts of a work entry, for the page that does not exist yet: what briar knew of the
    task before the page was there."""

    def of(self, entry: Optional[WorkEntry]) -> PageFile:
        if entry is None:
            return PageFile()
        facts: List[Fact] = []
        if entry.plan:
            facts.append(Fact.of("planned", entry.plan, "backfill"))
        if entry.pr:
            facts.append(Fact.of("pr-opened", f"PR #{entry.pr}", "backfill"))
        if entry.rounds:
            facts.append(Fact.of("rounds", f"the task had {entry.rounds} round(s)", "backfill"))
        facts += self._answered(entry)
        if entry.spent_by_day:
            facts.append(Fact.of("cost", self._spend(entry), "backfill"))
        if entry.report:
            facts.append(Fact.of("report", entry.report, "backfill"))
        return PageFile(facts)

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
        before the end of the task, where the state is not saved yet."""
        await asyncio.to_thread(self._add, kind, text, key)

    async def add_safe(self, kind: str, text: str, key: str = "") -> None:
        """A Fact of a task that already reached its end. A failure of the store only logs: a
        page is never worth a finished PR."""
        await self._safe(self.add(kind, text, key))

    async def notes(self, text: str) -> None:
        """The Notes of the task, written by the agent at the end of a workflow."""
        await self._safe(asyncio.to_thread(self._company.pages().set_notes, self._item.key, text))

    async def _safe(self, work: Awaitable[None]) -> None:
        """A write of the page of a task that reached its end: a failure of the store is logged
        and nothing else happens (the Facts that matter are re-derived from the entry)."""
        try:
            await work
        except OSError as failure:
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
        await self._facts.notes(notes[: self.LIMIT])

    def _text(self) -> str:
        """The previous Notes and the Facts of the task, as the prompt wants them."""
        page = self._facts.page()
        facts = "\n".join(fact.line() for fact in page.facts())
        return f"{page.notes()}\n\n{facts}"


__all__ = [
    "Fact",
    "PageBackfill",
    "PageFile",
    "PageName",
    "TaskFacts",
    "TaskNotes",
    "TaskPages",
]
