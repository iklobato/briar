"""Test-to-source ratio + source files with no obvious test.

Walks the repo file tree once and partitions every file into TEST vs
SOURCE (by directory convention + filename convention for tests, by
extension for source). The ratio is a cheap proxy for how much of the
codebase is exercised; the untested sample points an agent at the
files most likely to need a test when it touches them.

Best-effort by design: "has a test" is a substring match of the source
file's basename stem against any test file path — it over-counts (a
shared stem matches), never under-counts, so the untested sample is a
conservative floor, not a precise audit."""

from __future__ import annotations

import argparse
import os
import re
from typing import List

from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section

_SOURCE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rb", ".java", ".rs", ".kt"}
_TEST_DIR_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/", re.I)
_TEST_FILE_RE = re.compile(
    r"(^|/)(test_[^/]+|[^/]+_test\.[A-Za-z]+|[^/]+\.test\.[A-Za-z]+|[^/]+\.spec\.[A-Za-z]+)$",
    re.I,
)


def _is_test(path: str) -> bool:
    return bool(_TEST_DIR_RE.search(path) or _TEST_FILE_RE.search(path))


class ExtractTestDiscipline(RepoBackedExtractor):
    name = "test-discipline"
    heading = "Test discipline"
    description = "test-to-source file ratio and source files without an obvious test"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--testdisc-repo",
            action="append",
            default=[],
            help="Repository slug to analyse. Repeatable.",
        )
        parser.add_argument(
            "--testdisc-top-n",
            type=int,
            default=10,
            help="How many untested source files to surface per repo (default: 10)",
        )

    _availability_arg = "testdisc_repo"

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.testdisc_repo:
            section = self._analyse_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"Test discipline — {len(per_repo)} repo(s)",
            body=(
                "Test-to-source file ratio per repo, plus source files with "
                "no obvious test. When you add or change one of the untested "
                "files, add a test for it."
            ),
            subsections=per_repo,
        )

    def _analyse_repo(self, repo: str, args: argparse.Namespace, provider) -> ExtractedSection:
        entries = provider.list_tree(repo)
        files = [e.path for e in entries if e.is_file]

        test_files = [p for p in files if _is_test(p)]
        source_files = [p for p in files if os.path.splitext(p)[1].lower() in _SOURCE_EXT and not _is_test(p)]
        if not source_files:
            return empty_section()

        test_count = len(test_files)
        source_count = len(source_files)
        test_ratio = round(test_count / source_count, 2)

        untested: List[str] = []
        for path in source_files:
            stem = os.path.splitext(os.path.basename(path))[0]
            if not any(stem in t for t in test_files):
                untested.append(path)
        untested_sample = untested[: args.testdisc_top_n]

        body_parts: List[str] = [
            f"{source_count} source / {test_count} test files (ratio {test_ratio})",
        ]
        if untested_sample:
            body_parts.append("")
            body_parts.append("Source files with no obvious test:")
            for path in untested_sample:
                body_parts.append(f"- `{path}`")

        return ExtractedSection(
            title=repo,
            body="\n".join(body_parts),
            data={
                "repo": repo,
                "source_count": source_count,
                "test_count": test_count,
                "test_ratio": test_ratio,
                "untested_sample": untested_sample,
            },
        )
