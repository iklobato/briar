"""Per-repo language / framework / tooling detection.

This file is a thin orchestrator over `language_detectors/`. Each
detector reads exactly one manifest file (pyproject.toml, package.json,
go.mod, …) and reports its findings; the union becomes the repo
section. Adding a new language is one file in `language_detectors/` —
this orchestrator is unaware of how many detectors exist."""

from __future__ import annotations

import argparse
import base64
from typing import Any, Dict, List, Optional

from briar.extract._gh import GithubApi
from briar.extract.base import ExtractedSection, KnowledgeExtractor
from briar.extract.language_detectors import LANGUAGE_DETECTORS, FileReader


class ExtractCodebaseConventions(KnowledgeExtractor):
    @staticmethod
    def _read_repo_file(repo: str, path: str) -> Optional[str]:
        """Pull a file via the GitHub Contents API; None on 404 / non-file."""
        try:
            resp = GithubApi.get_json(f"/repos/{repo}/contents/{path}")
        except Exception:  # noqa: BLE001 — 404 is the common case
            return None
        if type(resp) is not dict or resp.get("type") != "file":
            return None
        raw = resp.get("content") or ""
        encoding = resp.get("encoding") or "base64"
        if encoding != "base64":
            return raw
        try:
            return base64.b64decode(raw).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return None

    name = "codebase-conventions"
    description = "language, test runner, linter, migration tool per repo"
    requires_github = True

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--conventions-repo", action="append", default=[],
            help="GitHub repo to detect conventions for. Repeatable.",
        )

    def is_available(self, args: argparse.Namespace) -> bool:
        return bool(args.conventions_repo) and bool(GithubApi.auth_token())

    def extract(self, args: argparse.Namespace) -> Optional[ExtractedSection]:
        subsections = [
            self._inspect_repo(repo, self._read_repo_file)
            for repo in args.conventions_repo
        ]
        return ExtractedSection(
            title=f"Codebase conventions — {len(subsections)} repo(s)",
            body=(
                "Agents must match the detected conventions when proposing "
                "changes — same test runner, same linter, same migration "
                "tool."
            ),
            subsections=subsections,
        )

    def _inspect_repo(
        self,
        repo: str,
        reader: FileReader,
    ) -> ExtractedSection:
        findings: Dict[str, Any] = {}
        for detector in LANGUAGE_DETECTORS:
            result = detector.detect(repo, reader)
            if result is not None:
                findings.update(result)
        if not findings:
            return ExtractedSection(title=repo, body="_no manifest detected_")
        lines = [f"- **{k}**: {v}" for k, v in sorted(findings.items())]
        return ExtractedSection(
            title=repo,
            body="\n".join(lines),
            data=findings,
        )
