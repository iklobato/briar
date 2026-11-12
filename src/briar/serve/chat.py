"""What people say in chat about the tickets `briar serve` may start (spec 008 PR 2, decisions
C1 to C14 in `docs/decisions/2026-11-12-briar-2-work-selection-pr2.md`).

`ChatReader` reads a `ChatSource` (Slack first): the messages that cite a ticket key give the
points of the score (C1, C3, C10), and for the 5 best tickets the threads that cite them or talk
about their title go to the selector model as data (C4, C13, C14)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, ClassVar, Dict, FrozenSet, List, Mapping, Protocol, Sequence, Set, Tuple

from briar.iac.runbook.models import ServeSettings
from briar.serve.work import WorkItem


@dataclass(frozen=True)
class ChatMessage:
    """One chat message: who wrote it (an id, and a name to show), where, and in which thread."""

    author: str
    channel: str
    thread: str
    text: str
    name: str = ""
    bot: bool = False
    # A direct or group direct message.
    direct: bool = False

    def line(self) -> str:
        return f"{self.name or self.author} in {self.channel}: {self.text}"


class ChatSource(Protocol):
    """A chat tool `serve` reads (Slack first, C1). Read only."""

    def owner(self) -> str:
        """The user id of the owner of the token."""

    def search_all(self, query: str) -> List[ChatMessage]:
        """Every message the search finds, newest first."""

    def search_top(self, query: str) -> List[ChatMessage]:
        """The first page of the search, most relevant first."""

    def thread(self, message: ChatMessage) -> List[ChatMessage]:
        """The whole thread of a message, the message itself when it has none."""


class Citations:
    """C10: the ticket keys a text cites. Jira `KEY-N` (the `/browse/KEY-N` link holds it too);
    GitHub `owner/repo#N` and the `github.com/owner/repo/issues/N` link; never a bare `#N`."""

    def __init__(self, projects: Sequence[str], repos: Sequence[str]) -> None:
        self._projects = projects
        self._repos = repos

    def keys(self, text: str) -> Set[str]:
        found = {f"{project}-{number}" for project in self._projects for number in re.findall(rf"\b{project}-(\d+)\b", text)}
        return found | {key for repo in self._repos for key in self._github(repo, text)}

    def queries(self, since: date) -> List[str]:
        """C1: one quoted search per project prefix and per repo."""
        return [f'"{prefix}" after:{since.isoformat()}' for prefix in [*(f"{project}-" for project in self._projects), *self._repos]]

    def _github(self, repo: str, text: str) -> Set[str]:
        name = re.escape(repo)
        numbers = re.findall(rf"(?<![\w./-]){name}#(\d+)\b", text) + re.findall(rf"github\.com/{name}/issues/(\d+)\b", text)
        return {f"{repo}#{number}" for number in numbers}


@dataclass(frozen=True)
class ChatAsks:
    """C3: per ticket key, the distinct people (not the token owner, not a bot) who cited it,
    and the messages that cite it."""

    people: Mapping[str, FrozenSet[str]] = field(default_factory=dict)
    messages: Mapping[str, Tuple[ChatMessage, ...]] = field(default_factory=dict)

    PER_PERSON: ClassVar[int] = 25
    CAP: ClassVar[int] = 50

    def points(self, key: str) -> int:
        return min(len(self.people.get(key, frozenset())) * self.PER_PERSON, self.CAP)

    def fingerprint(self) -> Tuple[str, ...]:
        """What changes the choice (C6): the people of each cited ticket."""
        return tuple(f"{key}:{','.join(sorted(names))}" for key, names in sorted(self.people.items()))


class TitleWords:
    """C13: the strong words of a ticket title, for a chat search."""

    # ponytail: a small English list; a title in another language keeps its short words out only by length.
    STOP: ClassVar[FrozenSet[str]] = frozenset(
        "the and for from with into over via not are was its this that can cannot should does when after before using add fix update new".split()
    )
    WORD: ClassVar["re.Pattern[str]"] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,}")

    def __init__(self, title: str) -> None:
        self._title = title

    def query(self, since: date) -> str:
        """The search, or "" when the title has no strong word."""
        words = [word for word in self.WORD.findall(self._title) if word.lower() not in self.STOP]
        return f"{' '.join(words)} after:{since.isoformat()}" if words else ""


class ChatReader:
    """Reads the chat of one company for the selector."""

    WINDOW_DAYS = 14
    # C13: measured on 39 real tickets, the first 10 hits held 22 of the 24 that cite the key.
    RELATED = 10

    def __init__(self, source: ChatSource, settings: ServeSettings, today: Callable[[], date]) -> None:
        self._source = source
        self._settings = settings
        self._today = today

    def asks(self) -> ChatAsks:
        owner = self._source.owner()
        citations = self._citations()
        # A message that cites a Jira key and a repo is found by two searches: keep it once.
        found = list(dict.fromkeys(message for query in citations.queries(self._since()) for message in self._source.search_all(query)))
        cited: Dict[str, List[ChatMessage]] = {}
        for message in found:
            for key in citations.keys(message.text):
                cited.setdefault(key, []).append(message)
        people = {key: frozenset(message.author for message in messages if message.author != owner and not message.bot) for key, messages in cited.items()}
        return ChatAsks({key: names for key, names in people.items() if names}, {key: tuple(messages) for key, messages in cited.items()})

    def talk(self, items: Sequence[WorkItem], asks: ChatAsks) -> Dict[str, List[str]]:
        """For each ticket: the lines of every thread that cites it or talks about its title (C4, C13)."""
        owner = self._source.owner()
        return {item.key: self._threads([*asks.messages.get(item.key, ()), *self._related(item, owner)]) for item in items}

    def _related(self, item: WorkItem, owner: str) -> List[ChatMessage]:
        query = TitleWords(item.title).query(self._since())
        hits = self._source.search_top(query) if query else []
        channels = self._settings.chat.slack.channels if self._settings.chat.slack else []
        return [hit for hit in hits if hit.direct or f"<@{owner}>" in hit.text or hit.channel in channels][: self.RELATED]

    def _threads(self, messages: Sequence[ChatMessage]) -> List[str]:
        roots = list(dict.fromkeys((message.channel, message.thread) for message in messages))
        firsts = {(message.channel, message.thread): message for message in reversed(messages)}
        return ["\n".join(reply.line() for reply in self._source.thread(firsts[root])) for root in roots]

    def _citations(self) -> Citations:
        return Citations(list(self._settings.jira), self._settings.repos)

    def _since(self) -> date:
        return self._today() - timedelta(days=self.WINDOW_DAYS)


__all__ = ["ChatAsks", "ChatMessage", "ChatReader", "ChatSource", "Citations", "TitleWords"]
