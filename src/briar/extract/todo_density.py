"""TODO/FIXME/HACK marker density across one or more repos.

Counts in-code TODO/FIXME/HACK markers and surfaces the files carrying
the most of them — a quick "where is the unfinished work" signal an
agent can use to weigh how risky a file is to touch.

Code-search is heavily rate-limited (and the provider caps results at a
single page), so this is a DENSITY signal, not an exhaustive count: the
totals reflect what one capped search returned, not every marker in the
tree."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from briar.extract._provider import CodeSearchHit
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section


class ExtractTodoDensity(RepoBackedExtractor):
    name = "todo-density"
    heading = "TODO/FIXME density"
    description = "count of TODO/FIXME/HACK markers and the files carrying the most"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--todo-repo",
            action="append",
            default=[],
            help="Repository slug to scan for TODO/FIXME/HACK markers. Repeatable.",
        )
        parser.add_argument(
            "--todo-max",
            type=int,
            default=200,
            help="Max code-search matches to fetch per repo (default: 200)",
        )
        parser.add_argument(
            "--todo-top-n",
            type=int,
            default=10,
            help="How many marker-heavy files to surface per repo (default: 10)",
        )

    _availability_arg = "todo_repo"

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.todo_repo:
            section = self._scan_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"TODO/FIXME density — {len(per_repo)} repo(s)",
            body=(
                "Where the unfinished work lives. Markers are TODO/FIXME/HACK "
                "from a single capped code search — treat as a density signal "
                "(which files are marker-heavy), not an exhaustive count."
            ),
            subsections=per_repo,
        )

    def _scan_repo(self, repo: str, args: argparse.Namespace, provider) -> ExtractedSection:
        hits: List[CodeSearchHit] = provider.search_code(
            repo,
            "TODO OR FIXME OR HACK",
            max_count=args.todo_max,
        )
        if not hits:
            return empty_section()

        total_markers = sum(h.matches for h in hits)
        files_with_markers = len(hits)
        top = sorted(hits, key=lambda h: h.matches, reverse=True)[: args.todo_top_n]
        top_files = [{"file_path": h.file_path, "matches": h.matches} for h in top]

        data: Dict[str, Any] = {
            "repo": repo,
            "total_markers": total_markers,
            "files_with_markers": files_with_markers,
            "top_files": top_files,
        }
        body_lines = [
            f"{total_markers} markers across {files_with_markers} file(s).",
        ]
        for f in top_files:
            body_lines.append(f"- `{f['file_path']}` ({f['matches']})")
        return ExtractedSection(
            title=repo,
            body="\n".join(body_lines),
            data=data,
        )
