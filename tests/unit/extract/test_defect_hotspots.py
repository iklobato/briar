"""Behaviour tests for the defect-hotspots compose layer.

`ExtractDefectHotspots` walks `provider.list_recent_commits` and ranks
files by a defect-risk score = churn × (1 + bug-fixes) × log(loc + 2).
Churn and bug-fix counts come from the commit list; LOC comes from a
capped batch of `read_file` calls (top-N candidates only).

The provider is mocked at the seams the composer calls — the
`list_recent_commits` and `read_file` verbs — using a hand-rolled
`RepositoryProvider` subclass, the same pattern `test_code_hotspots.py`
uses. The extractor isn't registered in `EXTRACTORS` yet, so it's
imported and instantiated directly.
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract._provider import Commit, RepositoryProvider
from briar.extract.defect_hotspots import ExtractDefectHotspots


def _args(**over):
    base = dict(
        risk_repo=["o/r"],
        risk_since_days=90,
        risk_max_commits=200,
        risk_top_n=10,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


class _CommitProvider(RepositoryProvider):
    """Minimal provider that implements only the verbs the defect-hotspots
    composer touches (`list_recent_commits`, `read_file`); every other
    abstract verb returns an inert value so the ABC can be instantiated."""

    kind = "fake"

    def __init__(
        self,
        commits=None,
        *,
        files=None,
        company: str = "",
        raises: Exception | None = None,
    ) -> None:
        self._company = company
        self._commits = commits or []
        # path -> file content; unknown paths read as empty (loc 0).
        self._files = files or {}
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
        return self._files.get(path, "")

    def list_recent_commits(self, repo, *, since_days=30, max_count=200):
        if self._raises is not None:
            raise self._raises
        return list(self._commits)


def _run(provider, args):
    ext = ExtractDefectHotspots()
    ext._provider = lambda a: provider  # type: ignore[assignment]
    return ext.extract(args)


@pytest.mark.unit
def test_ranks_by_risk_score_and_carries_data_fields():
    # big.py: churn 3, no bug-fixes, 100 loc.
    # small.py: churn 1, no bug-fixes, 2 loc.
    # big.py must out-rank small.py on both churn and size.
    commits = [
        Commit("s1", "alice", "add feature", "2026-06-01T00:00:00Z", ["big.py", "small.py"]),
        Commit("s2", "bob", "more work", "2026-06-02T00:00:00Z", ["big.py"]),
        Commit("s3", "alice", "refactor", "2026-06-03T00:00:00Z", ["big.py"]),
    ]
    files = {"big.py": "\n".join(f"line{i}" for i in range(100)), "small.py": "a\nb"}
    section = _run(_CommitProvider(commits, files=files), _args())

    assert section.title == "Defect hotspots — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"
    assert repo.data["commit_sample_size"] == 3

    rows = repo.data["top_risky"]
    assert [r["path"] for r in rows] == ["big.py", "small.py"]
    big = rows[0]
    assert big["commits"] == 3
    assert big["bugfix_commits"] == 0
    assert big["loc"] == 100
    assert big["risk_score"] > rows[1]["risk_score"]
    # Body carries a per-file risk bullet, not just the title.
    assert f"- `big.py` — risk **{big['risk_score']}**" in repo.body
    assert "churn 3, bug-fixes 0, 100 loc" in repo.body


@pytest.mark.unit
def test_bugfix_weighting_breaks_equal_churn_tie():
    # buggy.py and clean.py have EQUAL churn (2 each) and EQUAL size, but
    # buggy.py's commits are bug-fixes → it must rank higher.
    commits = [
        Commit("s1", "a", "fix crash on save", "2026-06-01T00:00:00Z", ["buggy.py"]),
        Commit("s2", "a", "bug: null deref", "2026-06-02T00:00:00Z", ["buggy.py"]),
        Commit("s3", "a", "add new endpoint", "2026-06-03T00:00:00Z", ["clean.py"]),
        Commit("s4", "a", "tidy imports", "2026-06-04T00:00:00Z", ["clean.py"]),
    ]
    body = "x\ny\nz"
    files = {"buggy.py": body, "clean.py": body}
    section = _run(_CommitProvider(commits, files=files), _args())

    rows = section.subsections[0].data["top_risky"]
    assert [r["path"] for r in rows] == ["buggy.py", "clean.py"]
    assert rows[0]["bugfix_commits"] == 2
    assert rows[1]["bugfix_commits"] == 0
    assert rows[0]["risk_score"] > rows[1]["risk_score"]


@pytest.mark.unit
def test_empty_upstream_yields_empty_section():
    section = _run(_CommitProvider([]), _args())
    assert section.is_empty
    assert section.title == ""


@pytest.mark.unit
def test_only_fileless_commits_yields_empty_section():
    commits = [Commit("s1", "a", "fix", "2026-06-01T00:00:00Z", [])]
    section = _run(_CommitProvider(commits), _args())
    assert section.is_empty


@pytest.mark.unit
def test_read_file_calls_capped_to_top_n():
    # 3 distinct files but top_n=1 → only the highest-churn file's content
    # should be read (read_file is the expensive verb we cap).
    reads: list = []
    commits = [
        Commit("s1", "a", "m", "2026-06-01T00:00:00Z", ["f1.py", "f2.py", "f3.py"]),
        Commit("s2", "a", "m", "2026-06-02T00:00:00Z", ["f1.py"]),
    ]

    class _Recording(_CommitProvider):
        def read_file(self, repo, path):
            reads.append(path)
            return "a\nb\nc"

    section = _run(_Recording(commits), _args(risk_top_n=1))
    rows = section.subsections[0].data["top_risky"]
    assert [r["path"] for r in rows] == ["f1.py"]
    assert reads == ["f1.py"]
