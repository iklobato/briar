"""Behaviour tests for the ci-health compose layer.

`ExtractCiHealth` walks `provider.list_ci_runs` and summarises pass
rate, flaky workflows, and run-duration trend over the completed-run
sample. The provider is mocked at the seam the composer calls — the
`RepositoryProvider.list_ci_runs` verb — using a hand-rolled
`RepositoryProvider` subclass, mirroring `test_code_hotspots.py`'s
`_CommitProvider`.
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract._provider import CiRun, RepositoryProvider
from briar.extract.ci_health import ExtractCiHealth


def _args(**over):
    base = dict(
        cihealth_repo=["o/r"],
        cihealth_limit=100,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


class _CiProvider(RepositoryProvider):
    """Minimal provider that only implements `list_ci_runs`; all other
    abstract verbs return inert values so the ABC can be instantiated."""

    kind = "fake"

    def __init__(self, runs=None, *, company: str = "") -> None:
        self._company = company
        self._runs = runs or []

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

    def list_ci_runs(self, repo, *, limit):
        return list(self._runs)


def _run(provider, args):
    ext = ExtractCiHealth()
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_pass_rate_and_median_duration():
    # 4 completed runs, 3 success → pass_rate 0.75.
    # Durations (minutes): 10, 20, 30, 40 → median 25.0.
    runs = [
        CiRun("test", "completed", "success", "main", "2026-06-01T00:00:00Z", "2026-06-01T00:10:00Z"),
        CiRun("test", "completed", "success", "main", "2026-06-01T00:00:00Z", "2026-06-01T00:20:00Z"),
        CiRun("test", "completed", "success", "main", "2026-06-01T00:00:00Z", "2026-06-01T00:30:00Z"),
        CiRun("test", "completed", "failure", "main", "2026-06-01T00:00:00Z", "2026-06-01T00:40:00Z"),
    ]
    section = _run(_CiProvider(runs), _args())

    assert section.title == "CI health — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"
    assert repo.data["completed_runs"] == 4
    assert repo.data["pass_rate"] == 0.75
    assert repo.data["median_run_minutes"] == 25.0
    assert "pass rate: **0.75**" in repo.body
    assert "median run duration: **25.0m**" in repo.body


@pytest.mark.unit
def test_workflow_with_success_and_failure_is_flagged_flaky():
    runs = [
        CiRun("build", "completed", "success", "main", "2026-06-01T00:00:00Z"),
        CiRun("build", "completed", "failure", "main", "2026-06-02T00:00:00Z"),
        # A stable workflow with only successes must NOT be flagged.
        CiRun("lint", "completed", "success", "main", "2026-06-01T00:00:00Z"),
    ]
    section = _run(_CiProvider(runs), _args())
    repo = section.subsections[0]
    assert repo.data["flaky_workflows"] == ["build"]
    assert repo.data["flaky_workflow_count"] == 1
    assert "build" in repo.body


@pytest.mark.unit
def test_retry_attempt_flags_flaky():
    # A single run that needed a second attempt is flaky on its own.
    runs = [
        CiRun("deploy", "completed", "success", "main", "2026-06-01T00:00:00Z", run_attempt=2),
    ]
    section = _run(_CiProvider(runs), _args())
    assert section.subsections[0].data["flaky_workflows"] == ["deploy"]


@pytest.mark.unit
def test_empty_upstream_yields_empty_section():
    section = _run(_CiProvider([]), _args())
    assert section.is_empty
    assert section.title == ""


@pytest.mark.unit
def test_only_in_flight_runs_yields_empty_section():
    # No conclusion and not completed → nothing to score.
    runs = [CiRun("test", "in_progress", "", "main", "2026-06-01T00:00:00Z")]
    section = _run(_CiProvider(runs), _args())
    assert section.is_empty
