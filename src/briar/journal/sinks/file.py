"""FileSink — write a polished Markdown summary per session.

Always-available (no creds, no network). Sits at
``<root>/published/<session-id>.md`` by default. Different artifact
from `FileJournalStore` (the system of record): the store writes JSON
for replay/forensics, this sink writes Markdown for humans to read or
paste into a PR description.

Adding NotionSink or SlackSink later = sibling modules + one
``JOURNAL_SINKS`` registry entry. Markdown rendering is shared with
`briar journal show` via `briar.journal._render`; per-API formats
(Notion blocks, Slack Block Kit) stay private to their sink."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from briar.decorators import swallow_errors
from briar.journal._render import render_markdown
from briar.journal.models import Session
from briar.journal.sinks.base import JournalSink


log = logging.getLogger(__name__)

_DEFAULT_ROOT = Path("./journal")


class FileSink(JournalSink):
    name = "file"

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = root or _DEFAULT_ROOT
        (self._root / "published").mkdir(parents=True, exist_ok=True)

    def is_available(self) -> bool:
        return True

    @swallow_errors(default=False, message="journal file-sink publish")
    def publish(self, session: Session) -> bool:
        if not session.closed:
            raise RuntimeError(f"session {session.session_id} is open; close before publishing")
        path = self._root / "published" / f"{session.session_id}.md"
        path.write_text(render_markdown(session))
        log.debug("file-sink published: session=%s path=%s", session.session_id, path)
        return True
