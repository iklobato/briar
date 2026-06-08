"""End-to-end: `briar extract` driving the REAL GithubProvider + PyGithub
requester against a wire-level mock of api.github.com. No function-seam mock —
the command, provider, pagination, parsing, and markdown rendering all run.

GitHub REST shapes: https://docs.github.com/en/rest/pulls/pulls#list-pull-requests
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_extract_pr_archaeology_real_github(cli, github_at, tmp_root) -> None:
    github_at.add(
        "GET",
        "/repos/acme/app/pulls",
        [
            {
                "number": 7,
                "title": "Fix login redirect",
                "state": "closed",
                "merged_at": "2026-01-02T10:00:00Z",
                "created_at": "2026-01-01T09:00:00Z",
                "updated_at": "2026-01-02T10:00:00Z",
                "user": {"login": "alice"},
                "labels": [{"name": "bug"}],
                "assignees": [{"login": "bob"}],
                "additions": 12,
                "deletions": 3,
            },
        ],
    )

    result = cli(
        "extract",
        "--company",
        "acme",
        "--include",
        "pr-archaeology",
        "--pr-repo",
        "acme/app",
        "--storage",
        "file",
        "--root",
        str(tmp_root / "knowledge"),
    )

    assert result.code == 0, result.err
    # The REAL client really called the REAL endpoint (auth header carried too).
    pulls_calls = [r for r in github_at.received if "/repos/acme/app/pulls" in r["path"]]
    assert pulls_calls, f"github never called; received={[r['path'] for r in github_at.received]}"
    assert pulls_calls[0]["headers"].get("Authorization", "").startswith("token ")
    # The seeded PR flowed through the REAL provider -> extractor -> markdown -> disk:
    # one merged PR by alice, with a 25h created->merged gap computed by the extractor.
    blob = "\n".join(p.read_text() for p in (tmp_root / "knowledge").rglob("*") if p.is_file())
    assert "merged PR sample: **1**" in blob
    assert "alice(1)" in blob
    assert "25.0h" in blob  # 2026-01-01T09:00 -> 2026-01-02T10:00
