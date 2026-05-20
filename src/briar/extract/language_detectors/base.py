"""`LanguageDetector` contract — one class per language.

A detector inspects a single manifest file (e.g. `pyproject.toml`) and
returns a findings dict (language, test_runner, linter, formatter,
migrations) or `None` if the manifest is absent.

The file-reader is injected so detectors are pure functions of
`(repo, reader)` — trivial to unit-test without network access."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, ClassVar, Dict, Optional


FileReader = Callable[[str, str], Optional[str]]


class LanguageDetector(ABC):
    """Subclasses set `name` + `manifest`, implement `detect`."""

    name: ClassVar[str] = ""
    manifest: ClassVar[str] = ""

    @abstractmethod
    def detect(
        self,
        repo: str,
        reader: FileReader,
    ) -> Optional[Dict[str, str]]:
        """Inspect the `manifest` file for `repo` via `reader`. Return
        findings or None when the manifest doesn't exist."""
