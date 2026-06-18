"""Behaviour tests for the commit-message-quality compose layer.

`ExtractCommitMessageQuality` walks `provider.list_recent_commits` and
scores each commit subject (`Commit.message`, already the first line)
against the Conventional Commits prefix grammar plus simple length
hygiene (too-long / too-short subjects).

The provider is mocked at the seam the composer calls — the
`RepositoryProvider.list_recent_commits` verb — using a hand-rolled
`RepositoryProvider` subclass, the same pattern `test_code_hotspots.py`
uses. `Commit.file_paths` is irrelevant here (this extractor only reads
the subject), so the canned commits leave it empty.
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract._provider import Commit, RepositoryProvider
from briar.extract.commit_message_quality import ExtractCommitMessageQuality


def _args(**over):
    base = dict(
        msg_repo=["o/r"],
        msg_since_days=90,
        msg_max_commits=200,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


class _CommitProvider(RepositoryProvider):
    """Minimal provider implementing only the verbs the composer touches;
    all other abstract verbs return inert values so the ABC instantiates."""

    kind = "fake"

    def __init__(self, commits=None, *, company: str = "") -> None:
        self._company = company
        self._commits = commits or []

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
        return list(self._commits)


def _run(provider, args):
    ext = ExtractCommitMessageQuality()
    ext._provider = lambda a: provider  # type: ignore[assignment]
    return ext.extract(args)


def _commit(message: str) -> Commit:
    return Commit("sha", "alice", message, "2026-06-01T00:00:00Z", [])


_LONG_SUBJECT = "feat: " + ("x" * 80)  # conventional AND > 72 chars


@pytest.mark.unit
def test_compose_scores_conventional_and_length_hygiene():
    # 4 commits: 2 conventional ("feat: add x", the long one), 2 not
    # ("updated stuff", "fix things"). One subject is > 72 chars.
    commits = [
        _commit("feat: add x"),  # conventional, len 11
        _commit("updated stuff"),  # not conventional, len 13
        _commit("fix things"),  # not conventional (no colon), len 10
        _commit(_LONG_SUBJECT),  # conventional, len 86 → too long
    ]
    section = _run(_CommitProvider(commits), _args())

    assert section.title == "Commit message quality — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"
    data = repo.data
    assert data["commit_sample_size"] == 4
    # 2 of 4 conventional → 0.5
    assert data["conventional_rate"] == 0.5
    # 1 of 4 over 72 chars → 0.25
    assert data["long_subject_rate"] == 0.25
    # none under 10 chars → 0.0
    assert data["short_subject_rate"] == 0.0
    # lengths 11, 13, 10, 86 → median (11+13)/2 = 12
    assert data["median_subject_length"] == 12
    assert "conventional-commits rate: **0.5**" in repo.body
    assert "commit sample: **4**" in repo.body


@pytest.mark.unit
def test_scoped_conventional_subject_counts_as_conventional():
    # A scoped prefix like "fix(api): ..." must match the grammar.
    commits = [_commit("fix(api): handle null token")]
    section = _run(_CommitProvider(commits), _args())
    assert section.subsections[0].data["conventional_rate"] == 1.0


@pytest.mark.unit
def test_empty_upstream_yields_empty_section():
    section = _run(_CommitProvider([]), _args())
    assert section.is_empty
    assert section.title == ""
