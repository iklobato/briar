"""Message-writer registry — Strategy + Factory.

Each writer wraps one vendor's outbound write API. Adding a new
vendor (Discord, Linear-comment, GitLab-MR-comment, …) = one module
+ one entry in the `(WriterClass, ...)` tuple here.

The factory `make_writer(kind, company, config)` is what `SendMessageTool`
calls at run time. Each runbook company's ``messages:`` block names
bindings by handle; the tool resolves handle → kind → factory."""

from __future__ import annotations

from typing import Any, Dict, Tuple, Type

from briar._registry import build_registry
from briar.errors import CliError
from briar.messaging._writer import MessageBindingResolved, MessageWriter, SendResult
from briar.messaging.bitbucket_pr_comment import BitbucketPrCommentWriter
from briar.messaging.github_pr_comment import GithubPrCommentWriter
from briar.messaging.jira_comment import JiraCommentWriter
from briar.messaging.jira_transition import JiraTransitionWriter
from briar.messaging.slack_channel import SlackChannelWriter
from briar.messaging.telegram_chat import TelegramChatWriter


WRITERS: Dict[str, Type[MessageWriter]] = build_registry(
    (
        JiraCommentWriter,
        JiraTransitionWriter,
        SlackChannelWriter,
        TelegramChatWriter,
        GithubPrCommentWriter,
        BitbucketPrCommentWriter,
    ),
    kind="message writer",
    name_attr="kind",
)


class MessageWriterRegistry:
    """Factory + introspection."""

    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(WRITERS.keys())

    @classmethod
    def make(cls, kind: str, *, company: str = "", config: Dict[str, Any] = None) -> MessageWriter:
        writer_cls = WRITERS.get(kind)
        if writer_cls is None:
            known = ", ".join(sorted(WRITERS.keys()))
            raise CliError(f"unknown message writer {kind!r}; known: {known}")
        return writer_cls(company=company, config=config or {})


make_writer = MessageWriterRegistry.make


__all__ = [
    "WRITERS",
    "MessageWriter",
    "MessageWriterRegistry",
    "MessageBindingResolved",
    "SendResult",
    "make_writer",
]
