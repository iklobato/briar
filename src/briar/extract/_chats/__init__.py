"""Chat provider registry.

Symmetric to `_meetings/`, `_trackers/` and `_providers/`. Adding a new
vendor (Discord, MS Teams, …) = one module + one entry in `CHATS`."""

from __future__ import annotations

from typing import Dict, Tuple, Type

from briar._registry import build_registry
from briar.errors import CliError
from briar.extract._chat import ChatProvider
from briar.extract._chats.slack import SlackChatProvider

CHATS: Dict[str, Type[ChatProvider]] = build_registry(
    (SlackChatProvider,),
    kind="chat provider",
    name_attr="kind",
)


def chat_kinds() -> Tuple[str, ...]:
    return tuple(CHATS.keys())


def make_chat(kind: str, company: str = "") -> ChatProvider:
    provider_cls = CHATS.get(kind)
    if provider_cls is None:
        known = ", ".join(sorted(CHATS.keys()))
        raise CliError(f"unknown chat provider {kind!r}; known: {known}")
    return provider_cls(company=company)


__all__ = ["CHATS", "ChatProvider", "chat_kinds", "make_chat"]
