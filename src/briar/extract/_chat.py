"""`ChatProvider` — vendor-neutral facade for team-chat tools (Slack, …).

Fourth source family alongside `RepositoryProvider` (code hosts),
`TrackerProvider` (issue trackers) and `MeetingProvider` (transcripts).
Chat is search-centric and thread-shaped — "find where we discussed X"
then "read that whole thread" — different verbs from PRs, tickets or
meetings, so a separate ABC keeps each contract honest (Liskov).

Concrete adapters live in `_chats/`. Today: Slack (read-only, via the
browser web-session credentials — see `_chats/slack.py`). Adding
another vendor (Discord, MS Teams, …) = one module + one registry
entry; zero extractor edits.

Extractor that consumes chat:
  ``slack-context``  JIT — top-K threads whose messages match a ticket
                     key / PR identifier, spliced into one agent run."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar, List

log = logging.getLogger(__name__)


# Default knobs for chat-context fetches — mirrored as argparse defaults
# in commands/agent.py. Keep them here so a single edit moves both the
# CLI defaults and the in-code fallbacks together.
DEFAULT_CHAT_TOP_K = 3
DEFAULT_CHAT_MAX_BYTES = 30_000


def human_ts(ts: str) -> str:
    """Render a Slack-style ``"1716422400.001234"`` epoch-seconds string
    as ``YYYY-MM-DD HH:MM``. Returns ``"?"`` for an empty/garbage ts so
    a single bad message never aborts a whole thread render."""
    if not ts:
        return "?"
    try:
        seconds = float(ts.split(".")[0])
    except ValueError:
        return "?"
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True)
class ChatMessage:
    """One message inside a thread. `author` is whatever display handle
    the vendor exposes (Slack: username, else the user/bot id)."""

    ts: str
    author: str
    text: str


@dataclass(frozen=True)
class ChatHit:
    """A single search match — the header `search_messages` returns.
    Body-less beyond the matched line so search stays cheap; the JIT
    extractor hydrates the full thread via `get_thread`."""

    channel_id: str
    channel_name: str
    ts: str
    text: str
    permalink: str = ""


@dataclass(frozen=True)
class ChatThread:
    """One thread WITH every message. Returned by `get_thread`. The
    `root_ts` is the ts used to fetch it (the matched message's ts);
    `messages` are ordered oldest-first."""

    channel_id: str
    channel_name: str
    root_ts: str
    messages: List[ChatMessage] = field(default_factory=list)
    permalink: str = ""


class ChatProvider(ABC):
    """Strategy contract. Concrete subclasses adapt one vendor onto a
    search verb and a thread-hydration verb.

    `is_available` gates the registry factory: a provider whose
    credentials are missing reports False and the extractor skips
    silently (matches `MeetingProvider` / `TrackerProvider`)."""

    kind: ClassVar[str] = ""

    @abstractmethod
    def is_available(self) -> bool:
        """True iff credentials are present for the company this
        provider was built for."""

    @abstractmethod
    def search_messages(self, *, query: str, max_count: int) -> List[ChatHit]:
        """Search every channel/DM the credentialed user can see for
        messages matching `query` (vendor's full query syntax allowed).
        Most-relevant first. Used by the JIT extractor to find threads
        relevant to a ticket key or PR identifier."""

    @abstractmethod
    def get_thread(self, *, channel_id: str, thread_ts: str, max_count: int) -> ChatThread:
        """Hydrate the full thread that `thread_ts` belongs to (passing
        any message ts in the thread returns the whole thread). A
        non-threaded message returns a single-message thread."""

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        """Env vars the doctor reports as required for this provider in
        the context of `company`. Mirrors `MeetingProvider`."""
        return []
