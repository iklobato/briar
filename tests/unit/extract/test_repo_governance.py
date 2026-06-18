"""Behaviour tests for the repo-governance compose layer.

`ExtractRepoGovernance` reports two governance signals per repo: the
branch-protection posture (`provider.get_branch_protection`) and whether
the project-hygiene files exist (`provider.read_file` returning non-empty).

The provider is mocked at the seams the composer calls — using a
hand-rolled `RepositoryProvider` subclass, the same pattern
`test_code_hotspots.py`'s `_CommitProvider` uses. A listed repo always
yields a non-empty subsection: the *absence* of a control is the signal,
so an unprotected, config-less repo is still reported.
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract._provider import BranchProtection, RepositoryProvider
from briar.extract.repo_governance import ExtractRepoGovernance


def _args(**over):
    base = dict(
        gov_repo=["o/r"],
        gov_branch="main",
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


class _GovProvider(RepositoryProvider):
    """Minimal provider implementing only the verbs the governance
    composer touches; all other abstract verbs return inert values so
    the ABC can be instantiated."""

    kind = "fake"

    def __init__(self, *, protection: BranchProtection, files=None, company: str = "") -> None:
        self._company = company
        self._protection = protection
        self._files = files or {}

    def is_available(self) -> bool:
        return True

    def resolve_token(self) -> str:
        return "fake-token"

    def clone_url(self, owner, repo):
        return f"https://fake/{owner}/{repo}.git"

    def authed_clone_url(self, owner, repo, token):
        return f"https://x:{token}@fake/{owner}/{repo}.git"

    def pr_creation_recipe(self, *, owner, repo, branch):
        return ""

    def list_pulls(self, repo, *, state, max_count):
        return []

    def read_file(self, repo, path):
        return self._files.get(path, "")

    def get_branch_protection(self, repo, branch=""):
        return self._protection


def _run(provider, args):
    ext = ExtractRepoGovernance()
    ext._provider = lambda a: provider  # type: ignore[assignment]
    return ext.extract(args)


@pytest.mark.unit
def test_protected_repo_with_codeowners_and_precommit():
    protection = BranchProtection(
        branch="main",
        exists=True,
        required_reviews=2,
        requires_status_checks=True,
        enforce_admins=True,
        requires_code_owner_review=True,
    )
    files = {
        ".github/CODEOWNERS": "* @team",
        ".pre-commit-config.yaml": "repos: []",
    }
    section = _run(_GovProvider(protection=protection, files=files), _args())

    assert section.title == "Repo governance — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"

    bp = repo.data["branch_protection"]
    assert bp == {
        "branch": "main",
        "exists": True,
        "required_reviews": 2,
        "requires_status_checks": True,
        "enforce_admins": True,
        "requires_code_owner_review": True,
    }
    assert repo.data["has_codeowners"] is True
    assert repo.data["has_precommit"] is True
    # No editorconfig / linter config supplied → those stay False.
    assert repo.data["has_editorconfig"] is False
    assert repo.data["has_linter_config"] is False

    assert "branch protection on `main`: ✓" in repo.body
    assert "CODEOWNERS present: ✓" in repo.body


@pytest.mark.unit
def test_unprotected_repo_with_no_config_is_still_present():
    # The absence of every control IS the signal — the subsection must
    # still be emitted, with all booleans False and exists=False.
    protection = BranchProtection(branch="main", exists=False)
    section = _run(_GovProvider(protection=protection, files={}), _args())

    assert not section.is_empty
    repo = section.subsections[0]
    assert repo.title == "o/r"

    bp = repo.data["branch_protection"]
    assert bp["exists"] is False
    assert bp["required_reviews"] == 0
    assert bp["requires_status_checks"] is False
    assert bp["enforce_admins"] is False
    assert bp["requires_code_owner_review"] is False

    assert repo.data["has_codeowners"] is False
    assert repo.data["has_precommit"] is False
    assert repo.data["has_editorconfig"] is False
    assert repo.data["has_linter_config"] is False

    assert "branch protection on `main`: ✗" in repo.body


@pytest.mark.unit
def test_no_repos_yields_empty_section():
    protection = BranchProtection(branch="main", exists=True)
    section = _run(_GovProvider(protection=protection), _args(gov_repo=[]))
    assert section.is_empty
    assert section.title == ""
