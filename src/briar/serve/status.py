"""The status file of `briar serve` (WP1 of the production plan, decisions P16 and P30).

`<knowledge>/work/<company>.status.json`, next to the lock of the company. It is written at the end of every poll, so an old
`updated_at` shows that `serve` is gone: a kill, a reboot or a crash leave no other trace. A refused login that stops `serve`
makes it `stopping` at once and `stopped` when the running tasks are done, with the reason, the source, the company and the fix.
Nothing leaves the machine: the file and the exit code 3 are the whole notice (P16). A write that fails is logged and never
stops `serve`."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional

from briar.serve.pause import DeadLogin
from briar.serve.work import ServeCompany


class ServeStatus:
    """The status file of one company."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    SUFFIX: ClassVar[str] = ".status.json"
    INDENT: ClassVar[int] = 2
    RUNNING: ClassVar[str] = "running"
    STOPPING: ClassVar[str] = "stopping"
    STOPPED: ClassVar[str] = "stopped"

    def __init__(self, company: ServeCompany) -> None:
        self._company = company

    def path(self) -> Path:
        return self._company.state_file().with_suffix(self.SUFFIX)

    def polled(self) -> None:
        """The end of a poll. Once the stop was asked it writes nothing: `stopping` and `stopped` are the last word."""
        if not self._company.stop.asked():
            self._write(self.RUNNING, None)

    def stopping(self, dead: DeadLogin) -> None:
        self._write(self.STOPPING, dead)

    def stopped(self, dead: DeadLogin) -> None:
        self._write(self.STOPPED, dead)

    def _write(self, state: str, dead: Optional[DeadLogin]) -> None:
        record = {"state": state, "company": self._company.name, "pid": os.getpid(), "updated_at": self._now(), "stop": self._stop(dead)}
        try:
            self._replace(record)
        except OSError as failure:
            self.LOG.warning("serve: %s: could not write the status file %s: %s", self._company.name, self.path(), failure)

    def _stop(self, dead: Optional[DeadLogin]) -> Optional[Dict[str, Any]]:
        from briar.commands._enums import ExitCode

        if dead is None:
            return None
        return {
            "source": dead.source,
            "company": dead.company,
            "detail": dead.detail,
            "fix": dead.fix,
            "exit_code": int(ExitCode.LOGIN_REFUSED),
            "at": self._now(),
        }

    def _replace(self, record: Dict[str, Any]) -> None:
        """Whole or not at all: a reader never sees half a file."""
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text(json.dumps(record, indent=self.INDENT) + "\n")
        os.replace(staging, path)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = ["ServeStatus"]
