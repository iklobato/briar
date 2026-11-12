"""A chat source with no network, for the tests of the chat asks (spec 008 PR 2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from briar.serve.chat import ChatMessage

OWNER = "U1"


@dataclass(frozen=True)
class FakeChat:
    """`ChatSource`: every message for the key searches, `related` for the title searches,
    `threads` by thread ts (a message alone when it has none); records the queries."""

    messages: List[ChatMessage]
    related: List[ChatMessage] = field(default_factory=list)
    threads: Dict[str, List[ChatMessage]] = field(default_factory=dict)
    queries: List[str] = field(default_factory=list)

    def owner(self) -> str:
        return OWNER

    def search_all(self, query: str) -> List[ChatMessage]:
        self.queries.append(query)
        return list(self.messages)

    def search_top(self, query: str) -> List[ChatMessage]:
        self.queries.append(query)
        return list(self.related)

    def thread(self, message: ChatMessage) -> List[ChatMessage]:
        return self.threads.get(message.thread, [message])
