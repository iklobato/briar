"""Task-scoped: fetch Slack thread(s) relevant to ONE specific task.

Invoked by `briar agent implement` and `briar agent prfix` at agent-
invocation time. Output is spliced into that single agent run's system
prompt — it does NOT go into the per-company knowledge blob.

  ``--slack-query <text>``  keyword search. Pulls the top-K threads
                            whose messages match ``text`` and hydrates
                            each one. The agent CLI defaults this to the
                            ticket key (implement) or the PR identifier
                            (prfix) when not set explicitly, so the agent
                            sees "what did the team say about ACME-123?"
                            without the operator having to find the
                            thread first.

Symmetric to `meeting_context.py` — same search-then-hydrate shape, same
per-item byte budget, same `ExtractedSection` output."""

from __future__ import annotations

import argparse
import logging
from typing import List

from briar.extract._chat import DEFAULT_CHAT_MAX_BYTES, DEFAULT_CHAT_TOP_K, ChatHit, ChatThread, human_ts
from briar.extract.base import ExtractedSection, TaskScopedChatExtractor, empty_section

log = logging.getLogger(__name__)


class FetchSlackContext(TaskScopedChatExtractor):
    name = "slack-context"
    heading = "Slack context"
    description = "Slack thread(s) relevant to one ticket or PR"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--slack-query",
            default="",
            help="Keyword search. Pulls top-K matching threads (Slack's full query syntax allowed).",
        )
        parser.add_argument(
            "--slack-top-k",
            type=int,
            default=DEFAULT_CHAT_TOP_K,
            help=f"Max threads to fetch in search mode (default: {DEFAULT_CHAT_TOP_K})",
        )
        parser.add_argument(
            "--slack-max-bytes",
            type=int,
            default=DEFAULT_CHAT_MAX_BYTES,
            help=f"Total thread-text byte cap (default: {DEFAULT_CHAT_MAX_BYTES})",
        )

    def fetch(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._chat(args)
        if not provider.is_available():
            log.info("slack-context: provider not available — skipping")
            return empty_section()

        query = (getattr(args, "slack_query", "") or "").strip()
        if not query:
            log.info("slack-context: no --slack-query passed — skipping")
            return empty_section()

        top_k = max(int(getattr(args, "slack_top_k", DEFAULT_CHAT_TOP_K)), 1)
        max_bytes = max(int(getattr(args, "slack_max_bytes", DEFAULT_CHAT_MAX_BYTES)), 1024)

        hits: List[ChatHit] = provider.search_messages(query=query, max_count=top_k)
        if not hits:
            log.info("slack-context: no matches for query=%r", query)
            return empty_section()

        # Hydrate each matched message into its full thread. Each
        # get_thread is wrapped in swallow_errors at the adapter — a
        # single failure returns None and we drop it, never abort the
        # whole fetch (the agent run is worth more than 100% recall).
        threads: List[ChatThread] = []
        for hit in hits:
            if not hit.channel_id or not hit.ts:
                continue
            thread = provider.get_thread(channel_id=hit.channel_id, thread_ts=hit.ts, max_count=_THREAD_MAX_MESSAGES)
            if thread is not None and thread.messages:
                threads.append(_with_hit_metadata(thread, hit))
        if not threads:
            return empty_section()

        per_thread_budget = max(max_bytes // len(threads), 2_000)
        parts: List[str] = [
            f"_Top {len(threads)} Slack thread(s) matching `{query[:120]}`. Treat decisions captured here as binding._",
            "",
        ]
        for thread in threads:
            parts.append(_render_thread(thread, per_thread_budget))
            parts.append("")

        return ExtractedSection(
            title=f"Slack context — {len(threads)} thread(s) for {query[:60]!r}",
            body="\n".join(parts),
            data={"query": query, "match_count": len(threads)},
        )


# A thread rarely runs past a few dozen messages; cap the hydration so a
# runaway megathread can't blow the byte budget before truncation kicks in.
_THREAD_MAX_MESSAGES = 50


def _with_hit_metadata(thread: ChatThread, hit: ChatHit) -> ChatThread:
    """The adapter's `get_thread` can't name the channel (the replies
    payload omits it) — graft the channel name + permalink from the
    search hit so the render has a human-readable header."""
    return ChatThread(
        channel_id=thread.channel_id,
        channel_name=hit.channel_name or thread.channel_name,
        root_ts=thread.root_ts,
        messages=thread.messages,
        permalink=hit.permalink or thread.permalink,
    )


def _render_thread(thread: ChatThread, max_bytes: int) -> str:
    """Markdown render of one thread, capped at `max_bytes`. The header
    always renders; the message body is what gets truncated."""
    channel = f"#{thread.channel_name}" if thread.channel_name else thread.channel_id
    lines: List[str] = [f"### {channel}  ({human_ts(thread.root_ts)})"]
    if thread.permalink:
        lines.append(f"**Link**: {thread.permalink}")
    lines.append("")
    for message in thread.messages:
        text = message.text.replace("\n", " ").strip()
        if not text:
            continue
        lines.append(f"- [{human_ts(message.ts)}] **{message.author}**: {text}")
    body = "\n".join(lines)

    encoded = body.encode("utf-8")
    if len(encoded) > max_bytes:
        log.info("slack-context thread %s truncated: %d -> %d bytes", thread.root_ts, len(encoded), max_bytes)
        # errors="replace" inserts U+FFFD at a boundary cut so multi-byte
        # characters (emoji, CJK) don't silently disappear.
        truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
        body = truncated + f"\n\n_…thread truncated at {max_bytes} bytes._"
    return body
