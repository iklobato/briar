"""Language detector registry.

Three shipped detectors (Python / Node / Go) + a frozen dataclass
that holds each one. Adding a new language (Rust / Ruby / Java /
Elixir / ...) = one entry in `LANGUAGE_DETECTORS` and one `_detect_*`
function below.

The ABC + per-detector-file structure that lived here previously
cost 4 files for 3 one-method classes — same shape Phase 9 collapsed
in `iac/scaffold/shapes/`. A dataclass with a `detect` callable
captures the same variation in one file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Tuple


FileReader = Callable[[str, str], str]


@dataclass(frozen=True)
class LanguageDetector:
    """Strategy via data, not via subclass. `detect(repo, reader)`
    returns findings dict; empty dict means "manifest absent."""

    name: str
    manifest: str
    detect: Callable[[str, FileReader], Dict[str, str]]


# ─── Python ──────────────────────────────────────────────────────────


# (needle, value, case_insensitive) — first match wins. Module-level
# so adding a migration framework needs no closure rebinding.
_PY_MIGRATION_PATTERNS: Tuple[Tuple[str, str, bool], ...] = (
    ("alembic", "alembic", False),
    ("django", "django", True),
)


def _detect_python(repo: str, reader: FileReader) -> Dict[str, str]:
    text = reader(repo, "pyproject.toml")
    if not text:
        return {}
    findings: Dict[str, str] = {"language": "python"}
    if "pytest" in text:
        findings["test_runner"] = "pytest"
    if "ruff" in text:
        findings["linter"] = "ruff"
    if "black" in text:
        findings["formatter"] = "black"
    for needle, value, case_insensitive in _PY_MIGRATION_PATTERNS:
        haystack = text.lower() if case_insensitive else text
        if needle in haystack:
            findings["migrations"] = value
            break
    return findings


# ─── Node ────────────────────────────────────────────────────────────


_NODE_TEST_RUNNER_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("jest", "jest"),
    ("vitest", "vitest"),
)


def _detect_node(repo: str, reader: FileReader) -> Dict[str, str]:
    text = reader(repo, "package.json")
    if not text:
        return {}
    findings: Dict[str, str] = {"language": "javascript"}
    if "typescript" in text:
        findings["language"] = "typescript"
    for needle, value in _NODE_TEST_RUNNER_PATTERNS:
        if needle in text:
            findings["test_runner"] = value
            break
    if "eslint" in text:
        findings["linter"] = "eslint"
    if "prettier" in text:
        findings["formatter"] = "prettier"
    if "knex" in text:
        findings["migrations"] = "knex"
    return findings


# ─── Go ──────────────────────────────────────────────────────────────


def _detect_go(repo: str, reader: FileReader) -> Dict[str, str]:
    text = reader(repo, "go.mod")
    if not text:
        return {}
    return {
        "language": "go",
        "test_runner": "go test",
        "formatter": "gofmt",
    }


# ─── Registry ────────────────────────────────────────────────────────


LANGUAGE_DETECTORS: Tuple[LanguageDetector, ...] = (
    LanguageDetector(name="python", manifest="pyproject.toml", detect=_detect_python),
    LanguageDetector(name="node", manifest="package.json", detect=_detect_node),
    LanguageDetector(name="go", manifest="go.mod", detect=_detect_go),
)


__all__ = ["LanguageDetector", "FileReader", "LANGUAGE_DETECTORS"]
