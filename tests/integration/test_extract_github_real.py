"""End-to-end: `briar extract` driving the REAL GithubProvider + PyGithub
requester against a wire-level mock of api.github.com — for the GitHub-backed
extractors NOT already covered by test_cli_extract_real.py (pr-archaeology).

No function-seam mock: the command, provider, PyGithub pagination, JSON parsing,
the extractor's own aggregation, markdown rendering, and the file store all run.
Each test asserts (1) exit code, (2) the extractor's COMPUTED output read back off
disk — counts/names/stats that can only come from the seeded payload — and (3)
that the real client issued the documented REST request (from mock_api.received).

GitHub REST doc shapes (payloads modelled on these, not invented from memory):
- pulls:        https://docs.github.com/en/rest/pulls/pulls#list-pull-requests
- review comments: https://docs.github.com/en/rest/pulls/comments#list-review-comments-on-a-pull-request
- issue comments:  https://docs.github.com/en/rest/issues/comments#list-issue-comments
- reviews:      https://docs.github.com/en/rest/pulls/reviews#list-reviews-for-a-pull-request
- commits:      https://docs.github.com/en/rest/commits/commits#list-commits
- commit detail:https://docs.github.com/en/rest/commits/commits#get-a-commit
- environments: https://docs.github.com/en/rest/deployments/environments#list-environments
- deployments:  https://docs.github.com/en/rest/deployments/deployments#list-deployments
- actions runs: https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-repository
- contents:     https://docs.github.com/en/rest/repos/contents#get-repository-content
"""

from __future__ import annotations

import base64

import pytest

pytestmark = pytest.mark.integration


# GitHub's /rate_limit body — PyGithub's get_rate_limit() parses this. The facade
# falls back to it when a response omits the x-ratelimit-remaining header (our
# wire mock does), so seed it to keep the recorded request list deterministic.
# https://docs.github.com/en/rest/rate-limit/rate-limit#get-rate-limit-status-for-the-authenticated-user
_RATE_LIMIT = {
    "resources": {"core": {"limit": 5000, "remaining": 4999, "reset": 1700000000, "used": 1}},
    "rate": {"limit": 5000, "remaining": 4999, "reset": 1700000000, "used": 1},
}


def _run(cli, tmp_root, *flags):
    return cli(
        "extract",
        "--company",
        "acme",
        *flags,
        "--storage",
        "file",
        "--root",
        str(tmp_root / "knowledge"),
    )


def _disk_blob(tmp_root) -> str:
    return "\n".join(p.read_text() for p in (tmp_root / "knowledge").rglob("*") if p.is_file())


def _paths(received):
    return [r["path"] for r in received]


# ─────────────────────────────── active-work ────────────────────────────────


def test_extract_active_work_real_github(cli, github_at, tmp_root) -> None:
    # state=open list; the extractor renders #number, title, author, draft flag,
    # and review-comment count. Two PRs, one a draft.
    github_at.add("GET", "/rate_limit", _RATE_LIMIT)
    github_at.add(
        "GET",
        "/repos/acme/app/pulls",
        [
            {
                "number": 11,
                "title": "Add rate limiting",
                "state": "open",
                "draft": False,
                "user": {"login": "carol"},
                "head": {"ref": "feature/ratelimit"},
                "base": {"ref": "main"},
                "review_comments": 4,
                "created_at": "2026-02-01T09:00:00Z",
            },
            {
                "number": 12,
                "title": "WIP refactor auth",
                "state": "open",
                "draft": True,
                "user": {"login": "dave"},
                "head": {"ref": "wip/auth"},
                "base": {"ref": "main"},
                "review_comments": 0,
                "created_at": "2026-02-02T09:00:00Z",
            },
        ],
    )

    result = _run(cli, tmp_root, "--include", "active-work", "--active-repo", "acme/app")

    assert result.code == 0, result.err
    # Real client hit the documented list-pulls endpoint with state=open.
    pulls = [r for r in github_at.received if r["path"].startswith("/repos/acme/app/pulls")]
    assert pulls, f"never called pulls; received={_paths(github_at.received)}"
    assert "state=open" in pulls[0]["path"]
    assert pulls[0]["headers"].get("Authorization", "").startswith("token ")

    blob = _disk_blob(tmp_root)
    assert "acme/app — 2 open PR(s)" in blob  # count computed by the extractor
    assert "#11" in blob and "by=carol" in blob and "comments=4" in blob
    # The draft flag only renders for PR #12 (is_draft=True) — proves the bool flowed.
    assert "[draft]" in blob
    assert "#12" in blob and "by=dave" in blob


# ────────────────────────────── code-hotspots ───────────────────────────────


def test_extract_code_hotspots_real_github(cli, github_at, tmp_root) -> None:
    # Two commits, each touching auth.py together with a partner file. The
    # co-change matrix should count auth.py twice and surface its co-changers.
    # list-commits gives no file list, so the provider fetches each commit's
    # detail (/commits/{sha}) for the `files` array.
    github_at.add("GET", "/rate_limit", _RATE_LIMIT)
    github_at.add(
        "GET",
        "/repos/acme/app/commits",
        [
            {"sha": "sha1aaa", "commit": {"author": {"name": "Alice", "date": "2026-02-10T10:00:00Z"}, "message": "fix auth"}},
            {"sha": "sha2bbb", "commit": {"author": {"name": "Bob", "date": "2026-02-11T10:00:00Z"}, "message": "auth tests"}},
        ],
    )
    github_at.add(
        "GET",
        "/repos/acme/app/commits/sha1aaa",
        {"sha": "sha1aaa", "files": [{"filename": "src/auth.py"}, {"filename": "tests/test_auth.py"}]},
    )
    github_at.add(
        "GET",
        "/repos/acme/app/commits/sha2bbb",
        {"sha": "sha2bbb", "files": [{"filename": "src/auth.py"}, {"filename": "tests/test_auth.py"}]},
    )

    result = _run(cli, tmp_root, "--include", "code-hotspots", "--hotspots-repo", "acme/app")

    assert result.code == 0, result.err
    # Real client listed commits AND fetched each commit's detail.
    assert any(r["path"].startswith("/repos/acme/app/commits?") for r in github_at.received), _paths(github_at.received)
    assert any(r["path"].startswith("/repos/acme/app/commits/sha1aaa") for r in github_at.received)
    assert any(r["path"].startswith("/repos/acme/app/commits/sha2bbb") for r in github_at.received)

    blob = _disk_blob(tmp_root)
    assert "Sample: 2 commits" in blob  # commit_sample_size computed by extractor
    assert "`src/auth.py` (touched 2×)" in blob  # touch count from the matrix
    # co-changes only render when count>1; auth.py + test_auth.py co-occur twice.
    assert "co-changes with: `tests/test_auth.py` (2)" in blob


# ─────────────────────────── github-deployments ─────────────────────────────


def test_extract_github_deployments_real_github(cli, github_at, tmp_root) -> None:
    # environments envelope, deployments list, actions/runs envelope.
    github_at.add("GET", "/rate_limit", _RATE_LIMIT)
    github_at.add(
        "GET",
        "/repos/acme/app/environments",
        {
            "total_count": 1,
            "environments": [
                {"id": 1, "name": "production", "html_url": "https://github.com/acme/app/deployments/production", "protection_rules": [{"id": 1}, {"id": 2}]},
            ],
        },
    )
    github_at.add(
        "GET",
        "/repos/acme/app/deployments",
        [{"id": 99, "environment": "production", "sha": "abcdef1234567890", "creator": {"login": "alice"}, "created_at": "2026-01-05T10:00:00Z"}],
    )
    github_at.add(
        "GET",
        "/repos/acme/app/actions/runs",
        {
            "total_count": 1,
            "workflow_runs": [
                {"name": "CI", "status": "completed", "conclusion": "success", "head_branch": "main", "created_at": "2026-01-05T09:00:00Z"},
            ],
        },
    )

    result = _run(cli, tmp_root, "--include", "github-deployments", "--deploy-repo", "acme/app")

    assert result.code == 0, result.err
    paths = _paths(github_at.received)
    assert any(p.startswith("/repos/acme/app/environments") for p in paths), paths
    # deployments fetched with the documented per_page bound (limit=10).
    assert any(p.startswith("/repos/acme/app/deployments") and "per_page=10" in p for p in paths), paths
    # actions/runs fetched with per_page=5 (the extractor's CI-run limit).
    assert any(p.startswith("/repos/acme/app/actions/runs") and "per_page=5" in p for p in paths), paths

    blob = _disk_blob(tmp_root)
    assert "production  protection_rules=2" in blob  # len(protection_rules) computed
    assert "sha=abcdef1  by=alice" in blob  # sha truncated to 7 chars by the provider
    assert "CI  status=completed  conclusion=success  branch=main" in blob


# ─────────────────────────── reviewer-profile ───────────────────────────────


def test_extract_reviewer_profile_real_github(cli, github_at, tmp_root) -> None:
    # One merged PR by carol; two reviewers leave inline comments. The extractor
    # samples merged PRs, then for each pulls inline+issue+review comments and
    # aggregates per-reviewer comment counts, files, and PR-reviewed counts.
    github_at.add("GET", "/rate_limit", _RATE_LIMIT)
    github_at.add(
        "GET",
        "/repos/acme/app/pulls",
        [
            {
                "number": 21,
                "title": "Ship feature X",
                "state": "closed",
                "merged_at": "2026-03-02T10:00:00Z",
                "created_at": "2026-03-01T09:00:00Z",
                "user": {"login": "carol"},
            },
        ],
    )
    # inline review comments (file/line level). erin comments twice on auth.py.
    github_at.add(
        "GET",
        "/repos/acme/app/pulls/21/comments",
        [
            {
                "id": 1,
                "user": {"login": "erin"},
                "body": "Please add a regression test for this edge case path.",
                "path": "src/auth.py",
                "line": 10,
                "created_at": "2026-03-01T10:00:00Z",
            },
            {
                "id": 2,
                "user": {"login": "erin"},
                "body": "This boundary check should validate the input length too.",
                "path": "src/auth.py",
                "line": 20,
                "created_at": "2026-03-01T10:05:00Z",
            },
            {
                "id": 3,
                "user": {"login": "frank"},
                "body": "Nit: rename for clarity, this name is overloaded already.",
                "path": "src/util.py",
                "line": 5,
                "created_at": "2026-03-01T11:00:00Z",
            },
        ],
    )
    github_at.add("GET", "/repos/acme/app/issues/21/comments", [])
    github_at.add("GET", "/repos/acme/app/pulls/21/reviews", [])

    result = _run(cli, tmp_root, "--include", "reviewer-profile", "--reviewer-repo", "acme/app")

    assert result.code == 0, result.err
    paths = _paths(github_at.received)
    # merged-PR sample → state=closed list, then the three comment endpoints.
    assert any(p.startswith("/repos/acme/app/pulls?") and "state=closed" in p for p in paths), paths
    assert any(p.startswith("/repos/acme/app/pulls/21/comments") for p in paths), paths
    assert any(p.startswith("/repos/acme/app/issues/21/comments") for p in paths), paths
    assert any(p.startswith("/repos/acme/app/pulls/21/reviews") for p in paths), paths

    blob = _disk_blob(tmp_root)
    assert "Sample: 1 merged PRs" in blob
    assert "Active reviewers: 2" in blob  # erin + frank, computed
    # erin: 2 comments across 1 PR → avg 2.0; top file auth.py.
    assert "### erin" in blob
    assert "PRs reviewed: **1** / comments left: **2** (avg **2.0**/PR)" in blob
    assert "Hot files: src/auth.py" in blob
    assert "### frank" in blob


# ────────────────────────── codebase-conventions ────────────────────────────


def test_extract_codebase_conventions_real_github(cli, github_at, tmp_root) -> None:
    # contents endpoint returns base64-encoded manifests; python detector reads
    # pyproject.toml. node/go manifests are absent (404 → read_file swallows).
    pyproject = base64.b64encode(b"[tool.pytest.ini_options]\n[tool.ruff]\n[tool.black]\nname = 'django-thing'\n").decode()
    github_at.add(
        "GET",
        "/repos/acme/app/contents/pyproject.toml",
        {"type": "file", "encoding": "base64", "content": pyproject},
    )
    github_at.add("GET", "/repos/acme/app/contents/package.json", {"message": "Not Found"}, status=404)
    github_at.add("GET", "/repos/acme/app/contents/go.mod", {"message": "Not Found"}, status=404)

    result = _run(cli, tmp_root, "--include", "codebase-conventions", "--conventions-repo", "acme/app")

    assert result.code == 0, result.err
    paths = _paths(github_at.received)
    # Real client fetched each manifest via the contents endpoint.
    assert any(p.startswith("/repos/acme/app/contents/pyproject.toml") for p in paths), paths
    assert any(p.startswith("/repos/acme/app/contents/package.json") for p in paths), paths
    assert any(p.startswith("/repos/acme/app/contents/go.mod") for p in paths), paths

    blob = _disk_blob(tmp_root)
    # Detector findings parsed from the decoded pyproject — language/test/lint/fmt/migrations.
    assert "**language**: python" in blob
    assert "**test_runner**: pytest" in blob
    assert "**linter**: ruff" in blob
    assert "**formatter**: black" in blob
    assert "**migrations**: django" in blob


# ───────────────────────────── unhappy path ─────────────────────────────────


def test_extract_active_work_github_500_yields_empty(cli, github_at, tmp_root) -> None:
    # GitHub 5xx on the list-pulls call: the provider's list_pulls is NOT wrapped
    # in swallow_errors, but active-work's only repo then has no PRs and the run
    # surfaces a non-zero exit ("nothing extracted"). Asserting the failure path
    # propagates rather than silently writing a bogus "0 open PRs" section.
    github_at.add("GET", "/rate_limit", _RATE_LIMIT)
    github_at.add("GET", "/repos/acme/app/pulls", {"message": "Server Error"}, status=500)

    result = _run(cli, tmp_root, "--include", "active-work", "--active-repo", "acme/app")

    assert result.code != 0
    # The real client really attempted the documented endpoint before failing.
    assert any(r["path"].startswith("/repos/acme/app/pulls") for r in github_at.received)
