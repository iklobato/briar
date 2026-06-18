"""Per-repo governance posture: branch protection + presence of the
project-hygiene files (CODEOWNERS, pre-commit, linter / editor config).

The signal here is often the *absence* of a control — a repo with no
branch-protection rule, no CODEOWNERS, no pre-commit hook is the loudest
governance smell. So a listed repo always yields a non-empty subsection;
the whole section is empty only when no repos were requested.

Provider-agnostic: this extractor talks to a `RepositoryProvider`, not
to GitHub directly. `get_branch_protection` / `read_file` carry the same
graceful-degradation contract on every provider (empty / `exists=False`
when the underlying API is absent)."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section

_CODEOWNERS_PATHS = ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"]
_LINTER_CONFIG_PATHS = ["ruff.toml", ".ruff.toml", ".flake8", ".eslintrc", ".eslintrc.json", "setup.cfg"]


class ExtractRepoGovernance(RepoBackedExtractor):
    name = "repo-governance"
    heading = "Repo governance"
    description = "branch protection + presence of CODEOWNERS, pre-commit, linter/editor config"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--gov-repo",
            action="append",
            default=[],
            help="Repository slug to inspect governance for. Repeatable.",
        )
        parser.add_argument(
            "--gov-branch",
            default="",
            help="Branch to check protection for (default: provider's default branch).",
        )

    _availability_arg = "gov_repo"

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = [self._inspect_repo(repo, args.gov_branch, provider) for repo in args.gov_repo]
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"Repo governance — {len(per_repo)} repo(s)",
            body=(
                "Branch-protection posture and the presence of project-hygiene "
                "files. An absent control (no protection, no CODEOWNERS, no "
                "pre-commit) is itself the signal — agents should respect the "
                "guardrails that exist and not assume missing ones."
            ),
            subsections=per_repo,
        )

    def _inspect_repo(self, repo: str, branch: str, provider) -> ExtractedSection:
        bp = provider.get_branch_protection(repo, branch)

        has_codeowners = any(provider.read_file(repo, p) for p in _CODEOWNERS_PATHS)
        has_precommit = bool(provider.read_file(repo, ".pre-commit-config.yaml"))
        has_editorconfig = bool(provider.read_file(repo, ".editorconfig"))
        has_linter_config = any(provider.read_file(repo, p) for p in _LINTER_CONFIG_PATHS)

        data: Dict[str, Any] = {
            "repo": repo,
            "branch_protection": {
                "branch": bp.branch,
                "exists": bp.exists,
                "required_reviews": bp.required_reviews,
                "requires_status_checks": bp.requires_status_checks,
                "enforce_admins": bp.enforce_admins,
                "requires_code_owner_review": bp.requires_code_owner_review,
            },
            "has_codeowners": has_codeowners,
            "has_precommit": has_precommit,
            "has_editorconfig": has_editorconfig,
            "has_linter_config": has_linter_config,
        }

        body_lines = [
            f"- branch protection on `{bp.branch}`: {self._mark(bp.exists)}",
            f"- required reviews: {bp.required_reviews}",
            f"- requires status checks: {self._mark(bp.requires_status_checks)}",
            f"- enforce admins: {self._mark(bp.enforce_admins)}",
            f"- requires code-owner review: {self._mark(bp.requires_code_owner_review)}",
            f"- CODEOWNERS present: {self._mark(has_codeowners)}",
            f"- pre-commit config present: {self._mark(has_precommit)}",
            f"- .editorconfig present: {self._mark(has_editorconfig)}",
            f"- linter config present: {self._mark(has_linter_config)}",
        ]
        return ExtractedSection(
            title=repo,
            body="\n".join(body_lines),
            data=data,
        )

    @staticmethod
    def _mark(flag: bool) -> str:
        return "✓" if flag else "✗"
