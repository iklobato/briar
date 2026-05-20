"""`LanguageDetector` contract — one class per language.

A detector inspects a single manifest file (e.g. `pyproject.toml`) and
returns a findings dict (language, test_runner, linter, formatter,
migrations) or `None` if the manifest is absent.

The file-reader is injected (rather than calling `_gh.get_json`
directly) so detectors are pure functions of `(repo, reader)` —
trivial to unit-test without network access."""

from __future__ import annotations

from typing import Callable, ClassVar, Dict, Optional


FileReader = Callable[[str, str], Optional[str]]


class LanguageDetector:
    """Subclasses set `name` + `manifest`, implement `detect`."""

    name: ClassVar[str] = ""
    manifest: ClassVar[str] = ""  # path of the manifest file to read

    def detect(
        self,
        repo: str,
        reader: FileReader,
    ) -> Optional[Dict[str, str]]:
        raise NotImplementedError
