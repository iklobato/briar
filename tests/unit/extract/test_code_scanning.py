"""Behaviour tests for the code-scanning compose layer.

`ExtractCodeScanning` walks `provider.list_code_scanning_alerts`, keeps
the open findings, and groups them by `rule_id`: per rule it reports the
firing count, the rule's severity, and one example file. Rules are
ranked by count and truncated to `--scan-top-n`.

The provider is mocked at the seam the composer calls — the
`RepositoryProvider.list_code_scanning_alerts` verb — using a
hand-rolled `RepositoryProvider` subclass whose other abstract verbs
return inert values so the ABC can be instantiated, the same pattern
`test_code_hotspots.py` uses.
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract._provider import RepositoryProvider, ScanAlert
from briar.extract.code_scanning import ExtractCodeScanning


def _args(**over):
    base = dict(
        scan_repo=["o/r"],
        scan_max=200,
        scan_top_n=10,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


class _ScanProvider(RepositoryProvider):
    """Minimal provider that only implements the verb the code-scanning
    composer touches; all other abstract verbs return inert values so
    the ABC can be instantiated."""

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

    def list_code_scanning_alerts(self, repo, *, max_count=200):
        return list(self._alerts)


def _run(provider, args):
    ext = ExtractCodeScanning()
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_groups_by_rule_with_counts_and_example_file():
    alerts = [
        ScanAlert("py/sql-injection", "high", "app/db.py", "SQLi", "open"),
        ScanAlert("py/sql-injection", "high", "app/orm.py", "SQLi", "open"),
        ScanAlert("py/weak-crypto", "medium", "app/crypto.py", "weak hash", "open"),
        # Dismissed findings must be filtered out entirely.
        ScanAlert("py/dead-rule", "low", "app/x.py", "noise", "dismissed"),
    ]
    section = _run(_ScanProvider(alerts), _args())

    assert section.title == "Code scanning alerts — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"
    assert repo.data["open_alert_count"] == 3  # dismissed dropped
    assert repo.data["by_severity"] == {"high": 2, "medium": 1}

    top = repo.data["top_rules"]
    # Ranking is load-bearing: sql-injection fires twice, so it leads.
    assert [r["rule_id"] for r in top] == ["py/sql-injection", "py/weak-crypto"]
    assert [r["count"] for r in top] == [2, 1]
    sqli = top[0]
    assert sqli["severity"] == "high"
    # Example file is the FIRST occurrence of the rule, not the last.
    assert sqli["example_file"] == "app/db.py"

    assert "3 open alert(s) across 2 rule(s)." in repo.body
    assert "- `py/sql-injection` ×2 (high) e.g. app/db.py" in repo.body


@pytest.mark.unit
def test_top_n_truncates_ranked_rules():
    alerts = [
        ScanAlert("r1", "high", "a.py", "m", "open"),
        ScanAlert("r1", "high", "b.py", "m", "open"),
        ScanAlert("r1", "high", "c.py", "m", "open"),
        ScanAlert("r2", "medium", "d.py", "m", "open"),
        ScanAlert("r2", "medium", "e.py", "m", "open"),
        ScanAlert("r3", "low", "f.py", "m", "open"),
    ]
    section = _run(_ScanProvider(alerts), _args(scan_top_n=2))
    top = section.subsections[0].data["top_rules"]
    # counts: r1=3, r2=2, r3=1 → top-2 keeps r1, r2 only.
    assert [r["rule_id"] for r in top] == ["r1", "r2"]
    # Truncation drops rules from the surfaced list but not the total.
    assert section.subsections[0].data["open_alert_count"] == 6


@pytest.mark.unit
def test_empty_upstream_yields_empty_section():
    section = _run(_ScanProvider([]), _args())
    assert section.is_empty
    assert section.title == ""


@pytest.mark.unit
def test_only_non_open_alerts_yields_empty_section():
    alerts = [ScanAlert("r1", "high", "a.py", "m", "fixed")]
    section = _run(_ScanProvider(alerts), _args())
    assert section.is_empty
