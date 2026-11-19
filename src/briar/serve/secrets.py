"""The scan of what a push would add (WP2 of the production plan, decisions P33 and P34).

A secret that reached the history of GitHub cannot be taken back, so every push of `serve` is preceded by a look at the lines it adds. The scan
reads a unified diff, follows the file and the line of each added line, and matches only the credential patterns of `SecretGuard` (AWS, GitHub,
Slack, PEM, JWT, a password in a URL). The name rule of `SecretGuard` is not used: `token` and `password` are everywhere in code. A hit is a file
and a line, never the value: the value stays out of the log, of the report and of the prompt of the agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar, Iterator, List, Tuple

from briar.extract._secrets import SecretGuard


@dataclass(frozen=True)
class Hit:
    """A line a push would add that looks like a credential: where it is, not what it says."""

    path: str
    line: int

    def label(self) -> str:
        return f"{self.path}:{self.line}"


class SecretScan:
    """Finds the added lines of a diff that hold a credential."""

    HUNK: ClassVar["re.Pattern[str]"] = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    FILE_START: ClassVar[str] = "diff --git "
    NEW_PATH: ClassVar[str] = "+++ b/"
    HEADER: ClassVar[str] = "+++ "
    ADDED: ClassVar[str] = "+"
    CONTEXT: ClassVar[str] = " "

    def __init__(self, guard: SecretGuard) -> None:
        self._guard = guard

    def findings(self, diff: str) -> List[Hit]:
        """One hit for each added line that holds a credential, in the order of the diff."""
        return [Hit(path, number) for path, number, text in self._added(diff) if self._credential(text)]

    def _credential(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self._guard.credential_values) or self._guard.url_password.search(text) is not None

    def _added(self, diff: str) -> Iterator[Tuple[str, int, str]]:
        """The added lines with their file and line number. A `+++ ` line is a header until the first hunk of its file."""
        path, number, header = "", 0, False
        for row in diff.splitlines():
            if row.startswith(self.FILE_START):
                path, header = "", True
                continue
            if header and row.startswith(self.HEADER):
                path = row[len(self.NEW_PATH) :] if row.startswith(self.NEW_PATH) else ""
                continue
            hunk = self.HUNK.match(row)
            if hunk is not None:
                number, header = int(hunk.group(1)), False
                continue
            if header:
                continue
            if row.startswith(self.ADDED):
                yield path, number, row[1:]
            number += 1 if row[:1] in (self.ADDED, self.CONTEXT) else 0


__all__ = ["Hit", "SecretScan"]
