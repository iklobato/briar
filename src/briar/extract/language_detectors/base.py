"""`LanguageDetector` contract — one class per language.

A detector inspects a single manifest file (e.g. `pyproject.toml`) and
returns a findings dict. Empty dict = "manifest not present, no
detection happened" — callers check truthiness.

The file-reader is injected so detectors are pure functions of
`(repo, reader)` — trivial to unit-test without network access. The
reader returns `""` for not-found instead of `None`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, ClassVar, Dict


FileReader = Callable[[str, str], str]


class LanguageDetector(ABC):
    """Subclasses set `name` + `manifest`, implement `detect`."""

    name: ClassVar[str] = ""
    manifest: ClassVar[str] = ""

    @abstractmethod
    def detect(self, repo: str, reader: FileReader) -> Dict[str, str]:
        """Inspect the `manifest` file for `repo` via `reader`. Return
        findings or `{}` when the manifest doesn't exist."""
