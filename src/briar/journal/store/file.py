"""Local-file journal store — one JSON file per session.

Layout: ``<root>/sessions/<YYYY-MM-DD>/<session-id>.json``. The dated
directory keeps long-running deployments from accumulating thousands of
files in one directory; the session-id filename is stable across
re-reads.

This is the system-of-record file format. The human-readable Markdown
summary lives on the publish side as `FileSink` — different file,
different purpose."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from briar.journal.models import Session
from briar.journal.store.base import JournalRef, JournalStore, JournalStoreBinding


log = logging.getLogger(__name__)

_DEFAULT_ROOT = Path("./journal")


class FileJournalStore(JournalStore):
    name = "file"

    def __init__(self, root: Path) -> None:
        self._root = root
        (self._root / "sessions").mkdir(parents=True, exist_ok=True)
        log.debug("file-journal-store init: root=%s", self._root)

    @classmethod
    def from_binding(cls, binding: JournalStoreBinding, *, default_root: Optional[Path] = None) -> "FileJournalStore":
        root = Path(binding.root) if binding.root else (default_root or _DEFAULT_ROOT)
        return cls(root)

    def _path_for(self, session_id: str, started_at: str) -> Path:
        day = (started_at or "")[:10] or "undated"
        return self._root / "sessions" / day / f"{session_id}.json"

    def _find_path(self, session_id: str) -> Optional[Path]:
        for path in (self._root / "sessions").rglob(f"{session_id}.json"):
            return path
        return None

    def put(self, session: Session) -> JournalRef:
        if not session.closed:
            raise RuntimeError(f"session {session.session_id} is open; close before persisting")
        path = self._path_for(session.session_id, session.started_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(session.to_dict(), indent=2))
        log.debug("file-journal-store put: session=%s path=%s", session.session_id, path)
        return JournalRef(
            session_id=session.session_id,
            command=session.command,
            target=session.target,
            started_at=session.started_at,
            ended_at=session.ended_at,
            decision_count=len(session.decisions),
        )

    def get(self, session_id: str) -> Optional[Session]:
        path = self._find_path(session_id)
        if path is None:
            return None
        return Session.from_dict(json.loads(path.read_text()))

    def list(self, *, command_prefix: str = "", limit: int = 50) -> List[JournalRef]:
        out: List[JournalRef] = []
        for path in sorted((self._root / "sessions").rglob("*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                log.exception("file-journal-store list: skipping unreadable %s", path)
                continue
            command = payload.get("command", "")
            if command_prefix and not command.startswith(command_prefix):
                continue
            out.append(
                JournalRef(
                    session_id=payload.get("session_id", ""),
                    command=command,
                    target=payload.get("target", ""),
                    started_at=payload.get("started_at", ""),
                    ended_at=payload.get("ended_at", ""),
                    decision_count=len(payload.get("decisions", []) or []),
                )
            )
            if len(out) >= limit:
                break
        return out
