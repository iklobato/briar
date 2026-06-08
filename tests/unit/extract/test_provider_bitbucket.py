"""Boundary tests for `BitbucketProvider` (_providers/bitbucket.py).

The provider talks to Bitbucket Cloud through `atlassian-python-api`'s
typed Cloud objects, obtained via `BitbucketProvider._repo(repo)`. We
patch `_repo` with a fake repository whose `.pullrequests` / `.pipelines`
/ `.commits` / `.deployment_environments` / `.get(...)` mimic the
library's iterator + envelope shapes. No real client, no network.
Assertions are on the normalised `_provider` dataclasses and request
params. The existing `tests/test_extract.py` covers `_to_pull`,
`_pipeline_failed`, and `_tail_pipeline_step_log`; this file fills the
verbs and failure modes.

Doc URLs modelled:
- Pull requests: https://developer.atlassian.com/cloud/bitbucket/rest/api-group-pullrequests/
- Deployment environments + deployments:
  https://developer.atlassian.com/cloud/bitbucket/rest/api-group-deployments/
- Pipelines: https://developer.atlassian.com/cloud/bitbucket/rest/api-group-pipelines/
- Source (file content): https://developer.atlassian.com/cloud/bitbucket/rest/api-group-source/
- Commits + diffstat: https://developer.atlassian.com/cloud/bitbucket/rest/api-group-commits/
- Paginated envelope `{"values":[...], "next": "url"}`:
  https://developer.atlassian.com/cloud/bitbucket/rest/intro/#pagination
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import pytest

from briar.extract._provider import CiRun, Commit, Deployment, Environment, PullRequest, ReviewComment
from briar.extract._providers.bitbucket import BitbucketProvider


def _provider():
    return BitbucketProvider(company="acme")


def _patch_repo(repo):
    return mock.patch.object(BitbucketProvider, "_repo", return_value=repo)


class _FakePr:
    """Duck-type of the library PullRequest object."""

    def __init__(self, **kw):
        self.id = kw.get("id", 7)
        self.title = kw.get("title", "Add cache")
        self.author = kw.get("author")
        self.source_branch = kw.get("source_branch", "fix/cache")
        self.destination_branch = kw.get("destination_branch", "main")
        self.comment_count = kw.get("comment_count", 0)
        self.created_on = kw.get("created_on", "2026-05-01T00:00:00Z")
        self.updated_on = kw.get("updated_on", "2026-05-02T00:00:00Z")
        self.reviewers = kw.get("reviewers", [])
        self.is_merged = kw.get("is_merged", False)
        self.data = kw.get("data", {})


# ---------------------------------------------------------------------------
# _resolve_addr (input boundary)
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ResolveAddrTests(unittest.TestCase):
    def test_with_slash(self) -> None:
        self.assertEqual(_provider()._resolve_addr("ws/repo"), ("ws", "repo"))

    def test_bare_uses_workspace(self) -> None:
        p = _provider()
        p._workspace_slug = "myws"
        self.assertEqual(p._resolve_addr("repo"), ("myws", "repo"))

    def test_bare_without_workspace_raises(self) -> None:
        p = _provider()
        p._workspace_slug = ""
        with self.assertRaises(RuntimeError):
            p._resolve_addr("repo")


# ---------------------------------------------------------------------------
# list_pulls
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListPullsTests(unittest.TestCase):
    def test_open_pulls_normalised(self) -> None:
        prs = [
            _FakePr(id=7, author=SimpleNamespace(display_name="Alice")),
            _FakePr(id=8, author=SimpleNamespace(display_name="Bob")),
        ]
        repo = mock.MagicMock()
        repo.pullrequests.each.return_value = iter(prs)
        with _patch_repo(repo):
            out = _provider().list_pulls("acme/app", state="open", max_count=10)
        self.assertEqual(len(out), 2)
        self.assertIsInstance(out[0], PullRequest)
        self.assertEqual(out[0].number, 7)
        self.assertEqual(out[0].author, "Alice")
        self.assertEqual(out[0].head_ref, "fix/cache")
        self.assertEqual(out[0].base_ref, "main")
        # open state → merged_at empty
        self.assertEqual(out[0].merged_at, "")

    def test_max_count_caps_iteration(self) -> None:
        prs = [_FakePr(id=n, author=SimpleNamespace(display_name="x")) for n in range(10)]
        repo = mock.MagicMock()
        repo.pullrequests.each.return_value = iter(prs)
        with _patch_repo(repo):
            out = _provider().list_pulls("acme/app", state="open", max_count=3)
        self.assertEqual(len(out), 3)

    def test_merged_state_sets_merged_at(self) -> None:
        repo = mock.MagicMock()
        repo.pullrequests.each.return_value = iter([_FakePr(id=1, author=SimpleNamespace(display_name="x"), updated_on="2026-06-01T00:00:00Z")])
        with _patch_repo(repo):
            out = _provider().list_pulls("acme/app", state="merged", max_count=10)
        self.assertEqual(out[0].merged_at, "2026-06-01T00:00:00Z")

    def test_empty_returns_empty(self) -> None:
        repo = mock.MagicMock()
        repo.pullrequests.each.return_value = iter([])
        with _patch_repo(repo):
            out = _provider().list_pulls("acme/app", state="open", max_count=10)
        self.assertEqual(out, [])

    def test_library_error_swallowed_to_empty(self) -> None:
        repo = mock.MagicMock()
        repo.pullrequests.each.side_effect = RuntimeError("401 Unauthorized")
        with _patch_repo(repo):
            out = _provider().list_pulls("acme/app", state="open", max_count=10)
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# list_environments
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListEnvironmentsTests(unittest.TestCase):
    def test_parses_environments(self) -> None:
        envs = [
            SimpleNamespace(data={"name": "Production", "restrictions_count": 2, "self_uri": "https://api/env/1"}, name="Production"),
            SimpleNamespace(data={"name": "Staging", "restrictions_count": 0, "self_uri": ""}, name="Staging"),
        ]
        repo = mock.MagicMock()
        repo.deployment_environments.each.return_value = iter(envs)
        with _patch_repo(repo):
            out = _provider().list_environments("acme/app")
        self.assertEqual(len(out), 2)
        self.assertIsInstance(out[0], Environment)
        self.assertEqual(out[0].name, "Production")
        self.assertEqual(out[0].protection_rule_count, 2)
        self.assertEqual(out[1].protection_rule_count, 0)

    def test_error_swallowed_to_empty(self) -> None:
        repo = mock.MagicMock()
        repo.deployment_environments.each.side_effect = RuntimeError("403")
        with _patch_repo(repo):
            self.assertEqual(_provider().list_environments("acme/app"), [])


# ---------------------------------------------------------------------------
# list_deployments
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListDeploymentsTests(unittest.TestCase):
    def test_parses_deployments(self) -> None:
        # https://developer.atlassian.com/cloud/bitbucket/rest/api-group-deployments/
        envelope = {
            "values": [
                {
                    "uuid": "{dep-1}",
                    "environment": {"name": "Production"},
                    "release": {"commit": {"hash": "abcdef1234567"}},
                    "deployer": {"display_name": "Deploy Bot"},
                    "created_on": "2026-05-01T00:00:00Z",
                }
            ]
        }
        repo = mock.MagicMock()
        repo.get.return_value = envelope
        with _patch_repo(repo):
            out = _provider().list_deployments("acme/app", limit=10)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], Deployment)
        self.assertEqual(out[0].id, "{dep-1}")
        self.assertEqual(out[0].environment, "Production")
        self.assertEqual(out[0].sha, "abcdef1")  # truncated to 7
        self.assertEqual(out[0].creator, "Deploy Bot")

    def test_non_dict_envelope_empty(self) -> None:
        repo = mock.MagicMock()
        repo.get.return_value = None
        with _patch_repo(repo):
            self.assertEqual(_provider().list_deployments("acme/app", limit=10), [])


# ---------------------------------------------------------------------------
# list_ci_runs
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListCiRunsTests(unittest.TestCase):
    def test_parses_pipelines(self) -> None:
        pipes = [
            SimpleNamespace(
                data={
                    "build_number": 42,
                    "target": {"ref_name": "main"},
                    "state": {"name": "COMPLETED", "result": {"name": "SUCCESSFUL"}},
                    "created_on": "2026-05-01T00:00:00Z",
                }
            )
        ]
        repo = mock.MagicMock()
        repo.pipelines.each.return_value = iter(pipes)
        with _patch_repo(repo):
            out = _provider().list_ci_runs("acme/app", limit=10)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], CiRun)
        self.assertEqual(out[0].name, "42")
        self.assertEqual(out[0].status, "COMPLETED")
        self.assertEqual(out[0].conclusion, "SUCCESSFUL")
        self.assertEqual(out[0].head_branch, "main")

    def test_limit_caps(self) -> None:
        pipes = [SimpleNamespace(data={"build_number": n, "state": {}}) for n in range(10)]
        repo = mock.MagicMock()
        repo.pipelines.each.return_value = iter(pipes)
        with _patch_repo(repo):
            out = _provider().list_ci_runs("acme/app", limit=2)
        self.assertEqual(len(out), 2)

    def test_error_swallowed_to_empty(self) -> None:
        repo = mock.MagicMock()
        repo.pipelines.each.side_effect = RuntimeError("500")
        with _patch_repo(repo):
            self.assertEqual(_provider().list_ci_runs("acme/app", limit=2), [])


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ReadFileTests(unittest.TestCase):
    def test_returns_string_from_default_branch(self) -> None:
        repo = mock.MagicMock()
        repo.data = {"mainbranch": {"name": "develop"}}
        repo.get.return_value = "file contents"
        with _patch_repo(repo):
            out = _provider().read_file("acme/app", "README.md")
        self.assertEqual(out, "file contents")
        # The src path uses the resolved default branch.
        path = repo.get.call_args.args[0]
        self.assertEqual(path, "src/develop/README.md")

    def test_decodes_bytes(self) -> None:
        repo = mock.MagicMock()
        repo.data = {}
        repo.get.return_value = b"raw bytes\nhere"
        with _patch_repo(repo):
            out = _provider().read_file("acme/app", "x")
        self.assertEqual(out, "raw bytes\nhere")

    def test_non_text_returns_empty(self) -> None:
        repo = mock.MagicMock()
        repo.data = {}
        repo.get.return_value = {"unexpected": "dict"}
        with _patch_repo(repo):
            self.assertEqual(_provider().read_file("acme/app", "x"), "")

    def test_error_swallowed_to_empty_string(self) -> None:
        repo = mock.MagicMock()
        repo.data = {}
        repo.get.side_effect = RuntimeError("404")
        with _patch_repo(repo):
            self.assertEqual(_provider().read_file("acme/app", "missing"), "")


# ---------------------------------------------------------------------------
# get_pull + list_pr_comments
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class GetPullAndCommentsTests(unittest.TestCase):
    def test_get_pull_detects_merged(self) -> None:
        repo = mock.MagicMock()
        repo.pullrequests.get.return_value = _FakePr(id=12, is_merged=True, author=SimpleNamespace(display_name="x"), updated_on="2026-06-01T00:00:00Z")
        with _patch_repo(repo):
            pr = _provider().get_pull("acme/app", 12)
        self.assertEqual(pr.number, 12)
        self.assertEqual(pr.merged_at, "2026-06-01T00:00:00Z")

    def test_get_pull_error_swallowed_to_none(self) -> None:
        repo = mock.MagicMock()
        repo.pullrequests.get.side_effect = RuntimeError("404")
        with _patch_repo(repo):
            self.assertIsNone(_provider().get_pull("acme/app", 12))

    def test_list_pr_comments_parses_inline_and_top_level(self) -> None:
        inline_comment = SimpleNamespace(
            data={
                "id": 1,
                "inline": {"path": "src/app.py", "to": 42},
                "content": {"raw": "needs a guard"},
                "user": {"display_name": "Alice"},
                "created_on": "2026-05-01T00:00:00Z",
            }
        )
        top_level = SimpleNamespace(
            data={
                "id": 2,
                "inline": {},
                "content": {"raw": "LGTM"},
                "user": {"nickname": "bob_nick"},
                "created_on": "2026-05-02T00:00:00Z",
            }
        )
        pr = mock.MagicMock()
        pr.comments.return_value = iter([inline_comment, top_level])
        repo = mock.MagicMock()
        repo.pullrequests.get.return_value = pr
        with _patch_repo(repo):
            out = _provider().list_pr_comments("acme/app", 7)
        self.assertEqual(len(out), 2)
        self.assertIsInstance(out[0], ReviewComment)
        self.assertEqual(out[0].author, "Alice")
        self.assertEqual(out[0].file_path, "src/app.py")
        self.assertEqual(out[0].line, 42)
        # top-level comment: nickname fallback, no file/line
        self.assertEqual(out[1].author, "bob_nick")
        self.assertEqual(out[1].file_path, "")
        self.assertEqual(out[1].line, 0)

    def test_list_pr_comments_error_swallowed_to_empty(self) -> None:
        repo = mock.MagicMock()
        repo.pullrequests.get.side_effect = RuntimeError("403")
        with _patch_repo(repo):
            self.assertEqual(_provider().list_pr_comments("acme/app", 7), [])


# ---------------------------------------------------------------------------
# list_recent_commits
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListRecentCommitsTests(unittest.TestCase):
    def test_parses_commits_with_diffstat_files(self) -> None:
        commits = [
            SimpleNamespace(
                data={
                    "hash": "abc123",
                    "message": "fix bug\n\nbody",
                    "date": "2026-05-01T00:00:00Z",
                    "author": {"user": {"display_name": "Alice"}},
                }
            )
        ]
        repo = mock.MagicMock()
        repo.commits.each.return_value = iter(commits)
        repo.get.return_value = {"values": [{"new": {"path": "src/a.py"}}, {"new": {"path": "src/b.py"}}]}
        with _patch_repo(repo):
            out = _provider().list_recent_commits("acme/app", max_count=10)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], Commit)
        self.assertEqual(out[0].sha, "abc123")
        self.assertEqual(out[0].author, "Alice")
        self.assertEqual(out[0].message, "fix bug")  # first line only
        self.assertEqual(out[0].file_paths, ["src/a.py", "src/b.py"])

    def test_skips_commits_without_hash(self) -> None:
        commits = [SimpleNamespace(data={"message": "x"}), SimpleNamespace(data={"hash": "z", "message": "y", "author": {}})]
        repo = mock.MagicMock()
        repo.commits.each.return_value = iter(commits)
        repo.get.return_value = {"values": []}
        with _patch_repo(repo):
            out = _provider().list_recent_commits("acme/app", max_count=10)
        self.assertEqual([c.sha for c in out], ["z"])

    def test_diffstat_failure_does_not_abort_commit(self) -> None:
        commits = [SimpleNamespace(data={"hash": "abc", "message": "m", "author": {}})]
        repo = mock.MagicMock()
        repo.commits.each.return_value = iter(commits)
        repo.get.side_effect = RuntimeError("diffstat 500")
        with _patch_repo(repo):
            out = _provider().list_recent_commits("acme/app", max_count=10)
        # Commit still emitted with empty file list (inner try/except).
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].file_paths, [])

    def test_error_swallowed_to_empty(self) -> None:
        repo = mock.MagicMock()
        repo.commits.each.side_effect = RuntimeError("500")
        with _patch_repo(repo):
            self.assertEqual(_provider().list_recent_commits("acme/app"), [])


# ---------------------------------------------------------------------------
# clone/auth seam + availability
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class SeamTests(unittest.TestCase):
    def test_authed_clone_url_uses_x_token_auth(self) -> None:
        url = _provider().authed_clone_url("acme", "app", "ATCTTtok")
        self.assertEqual(url, "https://x-token-auth:ATCTTtok@bitbucket.org/acme/app.git")

    def test_resolve_token_empty_without_company(self) -> None:
        self.assertEqual(BitbucketProvider().resolve_token(), "")

    def test_is_available_requires_all_creds(self) -> None:
        env = {"BITBUCKET_ACME_USERNAME": "u", "BITBUCKET_ACME_APP_PASSWORD": "p", "BITBUCKET_ACME_WORKSPACE": "w"}
        with mock.patch.dict("os.environ", env):
            self.assertTrue(BitbucketProvider(company="acme").is_available())

    def test_is_available_false_when_incomplete(self) -> None:
        with mock.patch.dict("os.environ", {"BITBUCKET_ACME_USERNAME": "u"}, clear=True):
            self.assertFalse(BitbucketProvider(company="acme").is_available())


@pytest.mark.boundary
class CloudConstructionTests(unittest.TestCase):
    """`_cloud()` picks basic-auth vs Bearer based on the x-token-auth sentinel."""

    def test_basic_auth_when_normal_username(self) -> None:
        env = {"BITBUCKET_ACME_USERNAME": "alice", "BITBUCKET_ACME_APP_PASSWORD": "pw", "BITBUCKET_ACME_WORKSPACE": "ws"}
        with mock.patch.dict("os.environ", env):
            provider = BitbucketProvider(company="acme")
            with mock.patch("atlassian.bitbucket.cloud.Cloud") as cloud_cls:
                cloud_cls.return_value = "client"
                first = provider._cloud()
                second = provider._cloud()  # cached
        self.assertIs(first, second)
        cloud_cls.assert_called_once()
        _, kwargs = cloud_cls.call_args
        self.assertEqual(kwargs["username"], "alice")
        self.assertEqual(kwargs["password"], "pw")
        self.assertNotIn("token", kwargs)

    def test_bearer_auth_for_x_token_auth_sentinel(self) -> None:
        # Workspace/repository access tokens reject basic auth → Bearer.
        env = {"BITBUCKET_ACME_USERNAME": "x-token-auth", "BITBUCKET_ACME_APP_PASSWORD": "ATCTTtok", "BITBUCKET_ACME_WORKSPACE": "ws"}
        with mock.patch.dict("os.environ", env):
            provider = BitbucketProvider(company="acme")
            with mock.patch("atlassian.bitbucket.cloud.Cloud") as cloud_cls:
                provider._cloud()
        _, kwargs = cloud_cls.call_args
        self.assertEqual(kwargs["token"], "ATCTTtok")
        self.assertNotIn("username", kwargs)


if __name__ == "__main__":
    unittest.main()
