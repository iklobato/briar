"""Behaviour tests for the revert-signals compose layer.

`ExtractRevertSignals` walks `provider.list_recent_commits` and scans
the commit subjects for revert commits ("Revert ...") and emergency-fix
language (hotfix, rollback, quick-fix). It reports the revert rate over
the sampled window and the files most often touched by a revert/hotfix
commit — the fragile areas an agent should be careful editing.

The provider is mocked at the seam the composer calls — the
`RepositoryProvider.list_recent_commits` verb — using a hand-rolled
`RepositoryProvider` subclass, the same pattern `test_code_hotspots.py`
uses.
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract._provider import Commit, RepositoryProvider
from briar.extract.revert_signals import ExtractRevertSignals


def _args(**over):
    base = dict(
        revert_repo=["o/r"],
        revert_since_days=90,
        revert_max_commits=200,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


class _CommitProvider(RepositoryProvider):
    """Minimal provider that only implements the verbs the revert-signals
    composer touches; all other abstract verbs return inert values so the
    ABC can be instantiated."""

    kind = "fake"

    def __init__(self, commits=None, *, company: str = "", raises: Exception | None = None) -> None:
        self._company = company
        self._commits = commits or []
        self._raises = raises

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
        return ""

    def list_recent_commits(self, repo, *, since_days=30, max_count=200):
        if self._raises is not None:
            raise self._raises
        return list(self._commits)


def _run(provider, args):
    ext = ExtractRevertSignals()
    # Patch the composer's _provider seam to return our fake.
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_counts_reverts_and_hotfixes_and_surfaces_fragile_files():
    commits = [
        Commit("s1", "alice", 'Revert "add cache layer"', "2026-06-01T00:00:00Z", ["cache.py"]),
        Commit("s2", "bob", "hotfix: null guard in parser", "2026-06-02T00:00:00Z", ["parser.py"]),
        Commit("s3", "carol", "feat: add export endpoint", "2026-06-03T00:00:00Z", ["export.py"]),
        Commit("s4", "dan", "Reverts the parser rewrite", "2026-06-04T00:00:00Z", ["parser.py"]),
    ]
    section = _run(_CommitProvider(commits), _args())

    assert section.title == "Revert & hotfix signals — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"
    assert repo.data["commit_sample_size"] == 4
    # s1 + s4 are reverts; s2 is a hotfix.
    assert repo.data["revert_count"] == 2
    assert repo.data["hotfix_count"] == 1
    assert repo.data["revert_rate"] == 0.5  # 2 / 4

    fragile = {f["path"]: f["count"] for f in repo.data["fragile_files"]}
    # parser.py implicated by both a hotfix (s2) and a revert (s4) → count 2.
    # cache.py by one revert (s1) → count 1. export.py never implicated.
    assert fragile == {"parser.py": 2, "cache.py": 1}
    assert "export.py" not in fragile
    assert "`parser.py` (2×)" in repo.body


@pytest.mark.unit
def test_normal_commit_message_is_not_counted_as_revert():
    # "reverberate" / "covert" must not trip the revert regex, and a plain
    # feature commit is neither a revert nor a hotfix.
    commits = [
        Commit("s1", "alice", "feat: covert reverberation telemetry", "2026-06-01T00:00:00Z", ["t.py"]),
    ]
    section = _run(_CommitProvider(commits), _args())
    repo = section.subsections[0]
    assert repo.data["revert_count"] == 0
    assert repo.data["hotfix_count"] == 0
    assert repo.data["revert_rate"] == 0.0
    assert repo.data["fragile_files"] == []


@pytest.mark.unit
def test_empty_upstream_yields_empty_section():
    section = _run(_CommitProvider([]), _args())
    assert section.is_empty
    assert section.title == ""
