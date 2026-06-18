"""Cluster recurring review asks across a repo's merged PRs.

Reviewers repeat the same kinds of asks — "add a test", "use a
constant instead of this magic number", "broad except, narrow it".
Each one is a candidate to codify as a lint rule or a documented
convention so the next PR never has to be told. This extractor maps
review-comment bodies onto a fixed set of canonical categories and
ranks them by how often they recur, turning "what gets flagged most"
into "what to automate next".

Provider-agnostic: same `RepositoryProvider` contract as
reviewer-profile — uses `list_pulls` + `list_pr_comments`."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from briar.extract._provider import ReviewComment
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section

# Canonical review-ask categories → lowercase keyword substrings. A
# comment is counted under a category if its (lowercased) body contains
# ANY of the category's substrings.
_CATEGORIES: Dict[str, List[str]] = {
    "missing test": ["add a test", "needs a test", "missing test", "write a test", "no test", "unit test"],
    "naming": ["rename", "better name", "naming", "confusing name", "clearer name"],
    "magic value / use constant": [
        "magic number",
        "magic string",
        "hardcode",
        "hard-code",
        "use a constant",
        "use an enum",
        "enum",
    ],
    "error handling": [
        "broad except",
        "bare except",
        "swallow",
        "error handling",
        "catch this",
        "handle the error",
        "try/except",
    ],
    "typing / annotations": ["type hint", "type annotation", "annotate", "typing", "missing type"],
    "docs / comments": ["docstring", "add a comment", "document this", "explain why", "comment explaining"],
    "simplify / dedupe": ["simplify", "duplicate", "duplicated", "extract this", "refactor", "dry this"],
    "edge case / null safety": [
        "edge case",
        "null check",
        "none check",
        "empty list",
        "boundary",
        "off by one",
        "off-by-one",
    ],
}

# Cap an example comment body so it stays prompt-safe.
_EXAMPLE_CAP = 160


class ExtractReviewNits(RepoBackedExtractor):
    name = "review-nits"
    heading = "Recurring review asks"
    description = "phrases reviewers repeat across PRs — candidates to codify as lint rules or conventions"
    requires_github = True  # legacy flag

    _availability_arg = "nits_repo"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--nits-repo",
            action="append",
            default=[],
            help="Repository slug to mine recurring review asks for. Repeatable.",
        )
        parser.add_argument(
            "--nits-pr-sample",
            type=int,
            default=30,
            help="How many recent merged PRs to sample per repo (default: 30)",
        )
        parser.add_argument(
            "--nits-top-n",
            type=int,
            default=15,
            help="How many recurring-ask categories to keep per repo (default: 15)",
        )

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.nits_repo:
            section = self._mine_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"Recurring review asks — {len(per_repo)} repo(s)",
            body=(
                "The asks reviewers repeat most across recent merged PRs, "
                "clustered into canonical categories. Each recurring ask is a "
                "candidate to codify as a lint rule or a documented convention "
                "so agents pre-empt the comment instead of earning it."
            ),
            subsections=per_repo,
        )

    def _mine_repo(self, repo: str, args: argparse.Namespace, provider) -> ExtractedSection:
        merged = provider.list_pulls(repo, state="merged", max_count=args.nits_pr_sample)
        if not merged:
            return empty_section()

        category_counts: Dict[str, int] = {cat: 0 for cat in _CATEGORIES}
        category_examples: Dict[str, str] = {}
        comment_count = 0
        for pr in merged:
            comments: List[ReviewComment] = provider.list_pr_comments(repo, pr.number)
            for c in comments:
                comment_count += 1
                lowered = c.body.lower()
                for category, keywords in _CATEGORIES.items():
                    if any(kw in lowered for kw in keywords):
                        category_counts[category] += 1
                        if category not in category_examples:
                            category_examples[category] = c.body[:_EXAMPLE_CAP]

        # Rank the typed (category, count) pairs first — int counts are
        # comparable; sorting the heterogeneous result dicts is not.
        ranked = sorted((p for p in category_counts.items() if p[1] > 0), key=lambda p: p[1], reverse=True)
        if not ranked:
            return empty_section()

        recurring: List[Dict[str, Any]] = [{"ask": cat, "count": count, "example": category_examples.get(cat, "")} for cat, count in ranked[: args.nits_top_n]]

        data: Dict[str, Any] = {
            "repo": repo,
            "pr_sample_size": len(merged),
            "comment_count": comment_count,
            "recurring_asks": recurring,
        }
        body_lines = [f'- **{r["ask"]}** ×{r["count"]} — e.g. "{r["example"]}"' for r in recurring]
        return ExtractedSection(
            title=repo,
            body="\n".join(body_lines),
            data=data,
        )
