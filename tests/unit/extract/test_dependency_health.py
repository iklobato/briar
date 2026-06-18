"""Behaviour tests for the dependency-health compose layer.

`ExtractDependencyHealth` walks `provider.list_dependabot_alerts` and
ranks the open alerts by severity (critical > high > medium > low, then
package name), surfacing the top 10 plus a stable per-severity count.

The provider is mocked at the seam the composer calls — the
`RepositoryProvider.list_dependabot_alerts` verb — using a hand-rolled
`RepositoryProvider` subclass, the same pattern `test_code_hotspots.py`'s
`_CommitProvider` uses. The class is imported directly (not via the
`EXTRACTORS` registry) and its `_provider` seam monkeypatched in `_run`.
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract._provider import RepositoryProvider, SecurityAlert
from briar.extract.dependency_health import ExtractDependencyHealth


def _args(**over):
    base = dict(
        deps_repo=["o/r"],
        deps_max=200,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


class _AlertProvider(RepositoryProvider):
    """Minimal provider implementing only the verb the dependency-health
    composer touches; all other abstract verbs return inert values so the
    ABC can be instantiated."""

    kind = "fake"

    def __init__(self, alerts=None, *, company: str = "") -> None:
        self._company = company
        self._alerts = alerts or []

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

    def list_dependabot_alerts(self, repo, *, max_count=200):
        return list(self._alerts)


def _run(provider, args):
    ext = ExtractDependencyHealth()
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_compose_counts_severities_and_orders_top_alerts():
    alerts = [
        SecurityAlert("low-pkg", "low", "minor", "open", "package.json"),
        SecurityAlert("crit-pkg", "critical", "rce", "open", "requirements.txt"),
        SecurityAlert("high-pkg", "high", "xss", "open", "package.json"),
        SecurityAlert("med-pkg", "medium", "dos", "open", "go.mod"),
    ]
    section = _run(_AlertProvider(alerts), _args())

    assert section.title == "Dependency health — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"
    assert repo.data["open_alert_count"] == 4
    # Stable severity shape — all four keys, 0-default.
    assert repo.data["by_severity"] == {"critical": 1, "high": 1, "medium": 1, "low": 1}

    # Ranking is load-bearing: critical sorts before low.
    order = [a["package"] for a in repo.data["top_alerts"]]
    assert order == ["crit-pkg", "high-pkg", "med-pkg", "low-pkg"]
    assert order.index("crit-pkg") < order.index("low-pkg")
    # Top alert carries manifest + summary.
    assert repo.data["top_alerts"][0]["manifest"] == "requirements.txt"
    # The body must carry the breakdown + a bullet per alert.
    assert "4 open (1 critical, 1 high, 1 medium, 1 low)" in repo.body
    assert "`crit-pkg` (critical): rce" in repo.body


@pytest.mark.unit
def test_non_open_alerts_are_filtered_out():
    alerts = [
        SecurityAlert("open-pkg", "high", "xss", "open", "package.json"),
        SecurityAlert("fixed-pkg", "critical", "rce", "fixed", "package.json"),
    ]
    section = _run(_AlertProvider(alerts), _args())
    repo = section.subsections[0]
    assert repo.data["open_alert_count"] == 1
    assert repo.data["by_severity"] == {"critical": 0, "high": 1, "medium": 0, "low": 0}
    packages = [a["package"] for a in repo.data["top_alerts"]]
    assert packages == ["open-pkg"]
    assert "fixed-pkg" not in repo.body


@pytest.mark.unit
def test_empty_upstream_yields_empty_section():
    section = _run(_AlertProvider([]), _args())
    assert section.is_empty
    assert section.title == ""


@pytest.mark.unit
def test_only_non_open_alerts_yields_empty_section():
    alerts = [SecurityAlert("fixed-pkg", "critical", "rce", "fixed", "package.json")]
    section = _run(_AlertProvider(alerts), _args())
    assert section.is_empty
