"""Boundary tests for `GithubProvider` (_providers/github.py).

Pure translation layer over `GithubApi`; mock at the facade
(`get_json` / `get_paginated`) and assert the normalised `_provider`
dataclasses. The existing `tests/test_extract.py` covers `_to_pull`,
`list_pr_comments` review-merging, and `list_ci_failures`; this file
fills the uncovered verbs (pulls/merged-filter, environments,
deployments, ci-runs, read_file base64, get_pull, recent commits) and
the documented failure modes.

Doc URLs modelled:
- List pulls: https://docs.github.com/en/rest/pulls/pulls#list-pull-requests
- Environments: https://docs.github.com/en/rest/deployments/environments
- Deployments: https://docs.github.com/en/rest/deployments/deployments
- Workflow runs: https://docs.github.com/en/rest/actions/workflow-runs
- Get content: https://docs.github.com/en/rest/repos/contents#get-repository-content
  (base64-encoded `content`, `encoding: base64`, `type: file`)
- List commits: https://docs.github.com/en/rest/commits/commits#list-commits
- Error envelope `{"message","documentation_url"}` → `github.GithubException`.
"""

from __future__ import annotations

import base64
import unittest
from unittest import mock

import pytest
from github import GithubException

from briar.extract._provider import CiRun, Commit, Deployment, Environment, PullRequest
from briar.extract._providers.github import GithubProvider, _validate_repo

# ---------------------------------------------------------------------------
# _validate_repo — boundary input validation (NOT swallowed: ValueError)
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ValidateRepoTests(unittest.TestCase):
    def test_accepts_owner_repo(self) -> None:
        _validate_repo("acme/app")  # no raise
        _validate_repo("a.b-c_d/x.y-z_w")

    def test_rejects_bare_name(self) -> None:
        with self.assertRaises(ValueError):
            _validate_repo("justrepo")

    def test_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            _validate_repo("acme/../etc")

    def test_list_pulls_propagates_validation_error(self) -> None:
        # @swallow_errors explicitly re-raises ValueError, so bad input is
        # NOT masked as an empty list.
        with self.assertRaises(ValueError):
            GithubProvider().list_pulls("notvalid", state="open", max_count=5)


# ---------------------------------------------------------------------------
# list_pulls
# ---------------------------------------------------------------------------


def _pull_row(**over):
    # https://docs.github.com/en/rest/pulls/pulls#list-pull-requests
    row = {
        "number": 7,
        "title": "Add cache",
        "user": {"login": "alice"},
        "draft": False,
        "head": {"ref": "fix/cache", "sha": "deadbeef"},
        "base": {"ref": "main"},
        "review_comments": 2,
        "created_at": "2026-05-01T00:00:00Z",
        "merged_at": None,
        "requested_reviewers": [{"login": "bob"}],
        "body": "summary",
    }
    row.update(over)
    return row


@pytest.mark.boundary
class ListPullsTests(unittest.TestCase):
    def test_open_pulls_normalised(self) -> None:
        rows = [_pull_row(), _pull_row(number=8, title="Second")]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            out = GithubProvider().list_pulls("acme/app", state="open", max_count=10)
        self.assertEqual(len(out), 2)
        self.assertIsInstance(out[0], PullRequest)
        self.assertEqual(out[0].number, 7)
        self.assertEqual(out[0].author, "alice")
        self.assertEqual(out[0].head_ref, "fix/cache")
        self.assertEqual(out[0].base_ref, "main")
        self.assertEqual(out[0].requested_reviewers, ["bob"])

    def test_merged_state_filters_unmerged_and_translates_query(self) -> None:
        captured = {}

        def fake(path, max_pages=50):
            captured["path"] = path
            return [
                _pull_row(number=1, merged_at="2026-05-02T00:00:00Z"),
                _pull_row(number=2, merged_at=None),  # closed-not-merged → dropped
                _pull_row(number=3, merged_at="2026-05-03T00:00:00Z"),
            ]

        with mock.patch("briar.extract._gh.GithubApi.get_paginated", side_effect=fake):
            out = GithubProvider().list_pulls("acme/app", state="merged", max_count=10)
        # GitHub has no "merged" state — provider queries state=closed.
        self.assertIn("state=closed", captured["path"])
        self.assertEqual([p.number for p in out], [1, 3])

    def test_max_count_caps(self) -> None:
        rows = [_pull_row(number=n) for n in range(1, 6)]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            out = GithubProvider().list_pulls("acme/app", state="open", max_count=2)
        self.assertEqual(len(out), 2)

    def test_empty_returns_empty(self) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=[]):
            out = GithubProvider().list_pulls("acme/app", state="open", max_count=10)
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# list_environments
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListEnvironmentsTests(unittest.TestCase):
    def test_parses_environments_with_protection_rule_count(self) -> None:
        # https://docs.github.com/en/rest/deployments/environments
        envelope = {
            "total_count": 2,
            "environments": [
                {"name": "production", "protection_rules": [{"id": 1}, {"id": 2}], "html_url": "https://github.com/acme/app/environments/production"},
                {"name": "staging", "protection_rules": [], "html_url": ""},
            ],
        }
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=envelope):
            out = GithubProvider().list_environments("acme/app")
        self.assertEqual(len(out), 2)
        self.assertIsInstance(out[0], Environment)
        self.assertEqual(out[0].name, "production")
        self.assertEqual(out[0].protection_rule_count, 2)
        self.assertEqual(out[1].protection_rule_count, 0)

    def test_non_dict_envelope_yields_empty(self) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=None):
            out = GithubProvider().list_environments("acme/app")
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# list_deployments
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListDeploymentsTests(unittest.TestCase):
    def test_parses_and_truncates_sha(self) -> None:
        # https://docs.github.com/en/rest/deployments/deployments
        rows = [
            {"id": 100, "environment": "production", "sha": "abcdef1234567890", "creator": {"login": "deploybot"}, "created_at": "2026-05-01T00:00:00Z"},
        ]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            out = GithubProvider().list_deployments("acme/app", limit=10)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], Deployment)
        self.assertEqual(out[0].id, "100")
        self.assertEqual(out[0].environment, "production")
        self.assertEqual(out[0].sha, "abcdef1")  # truncated to 7
        self.assertEqual(out[0].creator, "deploybot")

    def test_limit_caps_result(self) -> None:
        rows = [{"id": n, "sha": "x", "created_at": ""} for n in range(5)]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            out = GithubProvider().list_deployments("acme/app", limit=2)
        self.assertEqual(len(out), 2)


# ---------------------------------------------------------------------------
# list_ci_runs
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListCiRunsTests(unittest.TestCase):
    def test_parses_workflow_runs(self) -> None:
        # https://docs.github.com/en/rest/actions/workflow-runs
        envelope = {
            "total_count": 1,
            "workflow_runs": [
                {"name": "CI", "status": "completed", "conclusion": "success", "head_branch": "main", "created_at": "2026-05-01T00:00:00Z"},
            ],
        }
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=envelope):
            out = GithubProvider().list_ci_runs("acme/app", limit=10)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], CiRun)
        self.assertEqual(out[0].name, "CI")
        self.assertEqual(out[0].conclusion, "success")
        self.assertEqual(out[0].head_branch, "main")

    def test_limit_caps(self) -> None:
        envelope = {"workflow_runs": [{"name": str(n)} for n in range(5)]}
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=envelope):
            out = GithubProvider().list_ci_runs("acme/app", limit=3)
        self.assertEqual(len(out), 3)

    def test_non_dict_envelope_empty(self) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value="garbage"):
            out = GithubProvider().list_ci_runs("acme/app", limit=3)
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# read_file — base64 decoding + failure modes
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ReadFileTests(unittest.TestCase):
    def test_decodes_base64_content(self) -> None:
        # https://docs.github.com/en/rest/repos/contents#get-repository-content
        text = "name: ci\non: push\n"
        resp = {"type": "file", "encoding": "base64", "content": base64.b64encode(text.encode()).decode()}
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=resp):
            out = GithubProvider().read_file("acme/app", ".github/workflows/ci.yml")
        self.assertEqual(out, text)

    def test_returns_raw_when_not_base64_encoding(self) -> None:
        resp = {"type": "file", "encoding": "utf-8", "content": "plain text"}
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=resp):
            out = GithubProvider().read_file("acme/app", "README")
        self.assertEqual(out, "plain text")

    def test_directory_response_returns_empty(self) -> None:
        # A directory path returns a list, not a file dict.
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=[{"type": "dir"}]):
            out = GithubProvider().read_file("acme/app", "src")
        self.assertEqual(out, "")

    def test_404_returns_empty_string(self) -> None:
        # read_file has its own try/except — a 404 is the common "file
        # absent" case and must degrade to "".
        err = GithubException(404, {"message": "Not Found"}, {})
        with mock.patch("briar.extract._gh.GithubApi.get_json", side_effect=err):
            out = GithubProvider().read_file("acme/app", "missing.txt")
        self.assertEqual(out, "")

    def test_corrupt_base64_returns_empty(self) -> None:
        resp = {"type": "file", "encoding": "base64", "content": "!!!not base64!!!"}
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=resp):
            out = GithubProvider().read_file("acme/app", "x")
        self.assertEqual(out, "")


# ---------------------------------------------------------------------------
# get_pull
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class GetPullTests(unittest.TestCase):
    def test_returns_normalised_pull(self) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=_pull_row(number=12)):
            pr = GithubProvider().get_pull("acme/app", 12)
        self.assertEqual(pr.number, 12)

    def test_non_dict_falls_back_to_super(self) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=None):
            pr = GithubProvider().get_pull("acme/app", 12)
        # ABC default get_pull → number echoed back, empty fields.
        self.assertEqual(pr.number, 12)
        self.assertEqual(pr.title, "")

    def test_error_swallowed_to_none(self) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_json", side_effect=GithubException(500, {}, {})):
            pr = GithubProvider().get_pull("acme/app", 12)
        self.assertIsNone(pr)


# ---------------------------------------------------------------------------
# list_recent_commits
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListRecentCommitsTests(unittest.TestCase):
    def test_normalises_commits_and_first_line_message(self) -> None:
        # https://docs.github.com/en/rest/commits/commits#list-commits
        list_rows = [
            {"sha": "aaa111", "commit": {"author": {"name": "Alice", "date": "2026-05-01T00:00:00Z"}, "message": "fix bug\n\ndetails here"}},
        ]
        detail = {"files": [{"filename": "src/a.py"}, {"filename": "src/b.py"}]}

        with (
            mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=list_rows),
            mock.patch("briar.extract._gh.GithubApi.get_json", return_value=detail),
        ):
            out = GithubProvider().list_recent_commits("acme/app", since_days=30, max_count=10)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], Commit)
        self.assertEqual(out[0].sha, "aaa111")
        self.assertEqual(out[0].author, "Alice")
        self.assertEqual(out[0].message, "fix bug")  # first line only
        self.assertEqual(out[0].file_paths, ["src/a.py", "src/b.py"])

    def test_skips_rows_without_sha(self) -> None:
        list_rows = [{"commit": {"message": "x"}}, {"sha": "bbb", "commit": {"message": "y"}}]
        with (
            mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=list_rows),
            mock.patch("briar.extract._gh.GithubApi.get_json", return_value={"files": []}),
        ):
            out = GithubProvider().list_recent_commits("acme/app", max_count=10)
        self.assertEqual([c.sha for c in out], ["bbb"])

    def test_swallows_error_to_empty(self) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", side_effect=GithubException(403, {}, {})):
            out = GithubProvider().list_recent_commits("acme/app")
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# clone/auth seam + availability
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class SeamTests(unittest.TestCase):
    def test_authed_clone_url_uses_x_access_token(self) -> None:
        url = GithubProvider().authed_clone_url("acme", "app", "ghp_secret")
        self.assertEqual(url, "https://x-access-token:ghp_secret@github.com/acme/app.git")

    def test_clone_url(self) -> None:
        self.assertEqual(GithubProvider().clone_url("acme", "app"), "https://github.com/acme/app.git")

    def test_is_available_tracks_token(self) -> None:
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "x"}):
            self.assertTrue(GithubProvider().is_available())
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(GithubProvider().is_available())


if __name__ == "__main__":
    unittest.main()
