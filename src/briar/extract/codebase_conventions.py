"""Per-repo language / framework / tooling detection.

This file is a thin orchestrator over `language_detectors/`. Each
detector reads exactly one manifest file (pyproject.toml, package.json,
go.mod, …) and reports its findings; the union becomes the repo
section. Adding a new language is one file in `language_detectors/` —
this orchestrator is unaware of how many detectors exist.

Provider-agnostic: the detectors already take a `FileReader` callable.
This orchestrator just supplies one backed by the current
`RepositoryProvider.read_file`. Swap providers, same detectors."""

from __future__ import annotations

import argparse
from typing import Any, Dict

from briar.extract.base import ExtractedSection, RepoBackedExtractor
from briar.extract.language_detectors import LANGUAGE_DETECTORS, FileReader


class ExtractCodebaseConventions(RepoBackedExtractor):
    name = "codebase-conventions"
    description = "language, test runner, linter, migration tool per repo"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--conventions-repo",
            action="append",
            default=[],
            help="Repository slug to detect conventions for. Repeatable.",
        )

    def is_available(self, args: argparse.Namespace) -> bool:
        if not args.conventions_repo:
            return False
        try:
            provider = self._provider(args)
        except Exception:  # noqa: BLE001
            return False
        return provider.is_available()

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        reader: FileReader = provider.read_file
        subsections = [self._inspect_repo(repo, reader) for repo in args.conventions_repo]
        return ExtractedSection(
            title=f"Codebase conventions — {len(subsections)} repo(s)",
            body=("Agents must match the detected conventions when proposing " "changes — same test runner, same linter, same migration " "tool."),
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
