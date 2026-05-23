"""`BoardReader` — Strategy contract for "give me the cards on this
board" across tracker products.

Concrete readers live in `_boards/`. Each reader knows how to:

  * `matches(url)` — decide whether this reader recognises the URL.
  * `parse(url)`   — pull `(project, extras)` out of the URL.
  * `fetch(...)`   — return a list of `PlanCard` instances with title /
                     body / explicit dependencies filled in. Synthesis
                     (in-scope, out-of-scope, risks) is handled by the
                     plan command after this reader returns.

A reader returns *raw* cards; the command then enriches them, runs
dep-graph synthesis, and persists the result."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, List, Tuple

from briar.plan._models import PlanCard


@dataclass(frozen=True)
class BoardRef:
    """Result of `BoardReader.parse(url)`. `project` is the tracker
    project key; `owner` / `repo` are populated when the URL gives us
    a code-host hint (GitHub Projects); `extras` carries reader-specific
    fields (e.g. Jira board id, GH project number)."""

    tracker: str
    project: str
    url: str
    owner: str = ""
    repo: str = ""
    base_url: str = ""
    extras: Tuple[Tuple[str, str], ...] = ()

    def extra(self, key: str, default: str = "") -> str:
        for k, v in self.extras:
            if k == key:
                return v
        return default


class BoardReader(ABC):
    """One tracker family's board-URL adapter."""

    kind: ClassVar[str] = ""

    @abstractmethod
    def matches(self, url: str) -> bool:
        """True if this reader can handle the URL."""

    @abstractmethod
    def parse(self, url: str) -> BoardRef:
        """Pull project + tracker-specific fields out of the URL.
        Raises `CliError` on a malformed URL."""

    @abstractmethod
    def fetch(self, ref: BoardRef, *, company: str, max_cards: int) -> List[PlanCard]:
        """Return the board's cards, most-recent first. Each card must
        have at minimum `key`, `title`, `tracker`, and `url` populated;
        `summary` and `depends_on` are filled in where the source has
        them (Jira issue links, GH "Closes #N" body refs, etc.)."""
