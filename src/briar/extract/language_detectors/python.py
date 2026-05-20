"""Python detector — reads `pyproject.toml`."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from briar.extract.language_detectors.base import FileReader, LanguageDetector


# (needle, value, case_insensitive) — walked in order; first match wins.
_MIGRATION_PATTERNS: Tuple[Tuple[str, str, bool], ...] = (
    ("alembic", "alembic", False),
    ("django", "django", True),
)


class DetectPython(LanguageDetector):
    name = "python"
    manifest = "pyproject.toml"

    def detect(
        self,
        repo: str,
        reader: FileReader,
    ) -> Optional[Dict[str, str]]:
        text = reader(repo, self.manifest)
        if text is None:
            return None
        findings: Dict[str, str] = {"language": "python"}
        if "pytest" in text:
            findings["test_runner"] = "pytest"
        if "ruff" in text:
            findings["linter"] = "ruff"
        if "black" in text:
            findings["formatter"] = "black"
        for needle, value, case_insensitive in _MIGRATION_PATTERNS:
            haystack = text.lower() if case_insensitive else text
            if needle in haystack:
                findings["migrations"] = value
                break
        return findings
