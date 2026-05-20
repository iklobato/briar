"""Command base class + shared helpers.

Lives in its own module so sibling concretes can import it without
triggering the package `__init__` (which itself imports the concretes
to assemble the registry — a classic circular-import trap)."""

from __future__ import annotations

import argparse

from briar.http import ApiClient


class Command:
    """Implementation contract for every CLI verb.

    Subclasses set `name` + `help` and implement `add_arguments` /
    `run`. Subclasses must NOT touch the global registry or argparse
    state directly — the entry point in `briar.cli` is the only
    place that does that."""

    name: str = ""
    help: str = ""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Default: no extra arguments."""

    def run(
        self,
        client: ApiClient,
        args: argparse.Namespace,
    ) -> int:
        raise NotImplementedError


def confirm(prompt: str) -> bool:
    """Yes/no prompt; treats EOF (piped input) as a no."""
    try:
        return input(prompt).strip().lower() in {"y", "yes"}
    except EOFError:
        return False
