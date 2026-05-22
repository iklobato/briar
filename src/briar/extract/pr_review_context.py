"""Task-scoped: fetch full review context for ONE PR.

Invoked by `briar agent` when the operator passes a specific PR
number. Output is spliced into that single agent run's system prompt
so the pr-fixer archetype can see:

  - the PR's metadata (title, branches, draft status)
  - every comment thread (resolved + unresolved, inline + top-level)
  - failing CI steps with a log tail per failure

That's what the agent actually needs to address review feedback —
the scheduled `active-work` extractor only surfaces metadata."""

from __future__ import annotations

import argparse
import logging
from typing import List

from briar.extract._provider import CiFailure, ReviewComment
from briar.extract.base import EMPTY_SECTION, ExtractedSection, TaskScopedRepoExtractor


log = logging.getLogger(__name__)


class FetchPrReviewContext(TaskScopedRepoExtractor):
    name = "pr-review-context"
    description = "Full review context (comments + CI failures) for ONE specific PR"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--pr-target-repo",
            required=True,
            help="Repository slug (`owner/repo` for GitHub, `workspace/slug` for Bitbucket)",
        )
        parser.add_argument(
            "--pr-target-number",
            type=int,
            required=True,
            help="PR number to fetch context for",
        )

    def fetch(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        repo = args.pr_target_repo
        number = args.pr_target_number

        pr = provider.get_pull(repo, number)
        # Adapter's `@swallow_errors(default=None)` returns None on any
        # provider-side failure. The boundary that translates None →
        # EMPTY_SECTION lives at this caller, not inside the decorator.
        if pr is None or (not pr.title and not pr.head_ref):
            log.warning("pr-review-context: PR %s#%d not found", repo, number)
            return EMPTY_SECTION

        comments: List[ReviewComment] = provider.list_pr_comments(repo, number)
        failures: List[CiFailure] = provider.list_ci_failures(repo, number)

        body_parts: List[str] = [
            f"**PR**: {repo}#{number}",
            f"**Title**: {pr.title}",
            f"**Author**: {pr.author or '(unknown)'}",
            f"**Branch**: {pr.head_ref} → {pr.base_ref}",
        ]
        if pr.is_draft:
            body_parts.append("**Status**: DRAFT")
        if pr.requested_reviewers:
            body_parts.append(f"**Reviewers**: {', '.join(pr.requested_reviewers)}")

        # CI failures first — usually higher priority than comments.
        if failures:
            body_parts.append("")
            body_parts.append(f"### Failing CI ({len(failures)})")
            body_parts.append("")
            for f in failures:
                body_parts.append(f"**{f.workflow} → {f.job} → {f.step}**")
                if f.url:
                    body_parts.append(f"_{f.url}_")
                if f.log_tail:
                    body_parts.append("```")
                    body_parts.append(f.log_tail)
                    body_parts.append("```")
                body_parts.append("")

        if comments:
            inline = [c for c in comments if c.file_path]
            top_level = [c for c in comments if not c.file_path]

            if inline:
                body_parts.append(f"### Inline review comments ({len(inline)})")
                body_parts.append("")
                for c in inline[:30]:
                    body_parts.append(f"**{c.author}** on `{c.file_path}:{c.line}`:")
                    body_parts.append(c.body)
                    body_parts.append("")
                if len(inline) > 30:
                    body_parts.append(f"_…and {len(inline) - 30} more inline comments_")

            if top_level:
                body_parts.append(f"### PR-level comments ({len(top_level)})")
                body_parts.append("")
                for c in top_level[:15]:
                    body_parts.append(f"**{c.author}** ({c.created_at}):")
                    body_parts.append(c.body)
                    body_parts.append("")

        if not failures and not comments:
            body_parts.append("")
            body_parts.append("_No failing CI and no review comments — PR may already be ready to merge._")

        return ExtractedSection(
            title=f"PR review context — {repo}#{number}: {pr.title[:60]}",
            body="\n".join(body_parts),
            data={
                "pr_number": pr.number,
                "title": pr.title,
                "author": pr.author,
                "comment_count": len(comments),
                "failing_ci_count": len(failures),
            },
        )
