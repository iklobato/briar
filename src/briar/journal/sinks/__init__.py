"""Journal publish sinks — `JournalSink` Strategy + Registry.

One concrete sink today (`file`); Notion and Slack land as sibling
modules + one tuple entry each (Open/Closed). The registry uses
instances (matches `SOURCE_TEMPLATES`/`TRIGGER_TEMPLATES`/`ARCHETYPES`)
rather than classes (`NotificationSink`'s shape) because sinks here
take no per-call-site init args — the construction defaults are sane
for every site, and a sink that needs runtime config reads it from
`CredEnv` at first use."""

from __future__ import annotations

from typing import Dict

from briar._registry import build_registry
from briar.journal.sinks.base import JournalSink
from briar.journal.sinks.file import FileSink


JOURNAL_SINKS: Dict[str, JournalSink] = build_registry(
    (FileSink(),),
    kind="journal sink",
    name_attr="name",
)


__all__ = ["JOURNAL_SINKS", "JournalSink", "FileSink"]
