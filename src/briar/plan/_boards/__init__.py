"""Board-reader registry.

Adding a new tracker board (Linear, Trello, …) = one module here + one
entry in `BOARD_READERS`. The CLI's `briar plan build` picks the right
reader by URL match — no if-chain at the call site."""

from __future__ import annotations

from typing import Dict, Tuple

from briar._registry import build_registry
from briar.errors import CliError
from briar.plan._board import BoardReader, BoardRef
from briar.plan._boards.github_project import GithubProjectBoardReader
from briar.plan._boards.jira_board import JiraBoardReader


BOARD_READERS: Dict[str, BoardReader] = build_registry(
    (JiraBoardReader(), GithubProjectBoardReader()),
    kind="board reader",
    name_attr="kind",
)


class BoardReaderRegistry:
    """Factory + introspection."""

    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(BOARD_READERS.keys())

    @classmethod
    def resolve(cls, url: str) -> BoardReader:
        """Return the reader whose `matches(url)` returns True. Raises
        `CliError` when no reader recognises the URL — the error lists
        the registered kinds so the operator sees what was tried."""
        for reader in BOARD_READERS.values():
            if reader.matches(url):
                return reader
        known = ", ".join(sorted(BOARD_READERS.keys()))
        raise CliError(f"no board reader recognised URL {url!r}; registered: {known}")


resolve_board = BoardReaderRegistry.resolve


__all__ = [
    "BOARD_READERS",
    "BoardReader",
    "BoardReaderRegistry",
    "BoardRef",
    "resolve_board",
]
