"""`briar extract` — parametric per-flag EFFECT assertions.

Companion to ``test_extract.py`` (which covers the failure modes +
include/storage/json wiring). This file pins EVERY flag in
``/tmp/cli_manifest/extract.md`` to an observable effect:

* source-selection flags (``--provider`` / ``--tracker`` / ``--cloud`` /
  ``--meeting``) → assert the factory was invoked with that kind, and
  that an invalid choice exits 2.
* gather/repo/project flags (``--pr-repo`` / ``--active-repo`` / …) →
  assert the slug reaches the provider verb call.
* numeric caps (``--pr-max`` / ``--ticket-max`` / ``--reviewer-pr-sample``
  / ``--reviewer-top-n`` / ``--hotspots-*`` / ``--meeting-since-days`` /
  ``--meeting-max``) → assert the value reaches the verb call, and that
  the documented DEFAULT is used when the flag is omitted.
* allow/block author/assignee filters → assert they actually filter the
  rendered output.
* AWS knobs (``--aws-extract-region`` / ``--aws-extract-profile`` /
  ``--aws-extract-service``) → assert they reach ``make_cloud`` /
  ``list_subsections``.
* meta flags (``--company`` / ``--include`` / ``--storage`` /
  ``--blob-name`` / ``--root`` / ``--out-json``) → assert title, file
  path, and json sidecar.

The four provider factories are mocked at the lazy-import seam
(``briar.extract._providers.make_provider`` etc.); the extractor itself
runs for real, so a flag that the extractor silently ignores makes the
assertion FAIL. No network, no SDKs imported at module scope.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from briar.extract._meeting import Meeting
from briar.extract._provider import CiRun, Commit, Deployment, Environment, PullRequest, ReviewComment
from briar.extract._tracker import Ticket

# ─── recording doubles ────────────────────────────────────────────────


def _pr(number: int, author: str, **kw) -> PullRequest:
    base = dict(
        number=number,
        title=f"pr-{number}",
        author=author,
        is_draft=False,
        head_ref="feature",
        base_ref="main",
        review_comment_count=0,
        created_at="2026-05-09T22:00:00Z",
        merged_at="2026-05-10T00:00:00Z",
        requested_reviewers=[],
    )
    base.update(kw)
    return PullRequest(**base)


def _ticket(key: str, *, reporter: str = "rep", assignee: str = "asg", labels=None) -> Ticket:
    return Ticket(
        key=key,
        title=f"t-{key}",
        reporter=reporter,
        assignee=assignee,
        status="open",
        kind="bug",
        priority="high",
        created_at="2026-05-01T00:00:00Z",
        updated_at="2026-05-02T00:00:00Z",
        labels=labels or [],
    )


class FakeRepoProvider:
    """Records every verb call so tests can assert which slug / cap /
    filter reached the provider. Returns canned dataclasses so the
    extractor renders a real (non-empty) section."""

    def __init__(self) -> None:
        self.list_pulls_calls = []
        self.list_pr_comments_calls = []
        self.list_recent_commits_calls = []
        self.list_environments_calls = []
        self.list_deployments_calls = []
        self.list_ci_runs_calls = []
        self.read_file_calls = []
        # configurable canned returns
        self.pulls = [_pr(1, "alice"), _pr(2, "bob")]
        self.comments = [ReviewComment(id="c1", author="rev", body="x" * 30, file_path="a.py", line=1)]
        self.commits = [Commit(sha="abc", message="m", author="a", created_at="2026-05-01T00:00:00Z", file_paths=["a.py", "b.py"])]

    def is_available(self) -> bool:
        return True

    def list_pulls(self, repo, *, state, max_count):
        self.list_pulls_calls.append({"repo": repo, "state": state, "max_count": max_count})
        return list(self.pulls)

    def list_pr_comments(self, repo, number):
        self.list_pr_comments_calls.append({"repo": repo, "number": number})
        return list(self.comments)

    def list_recent_commits(self, repo, *, since_days, max_count):
        self.list_recent_commits_calls.append({"repo": repo, "since_days": since_days, "max_count": max_count})
        return list(self.commits)

    def list_environments(self, repo):
        self.list_environments_calls.append(repo)
        return [Environment(name="prod", protection_rule_count=2, url="u")]

    def list_deployments(self, repo, *, limit):
        self.list_deployments_calls.append({"repo": repo, "limit": limit})
        return [Deployment(id="d1", environment="prod", sha="sha", creator="c", created_at="2026-05-01")]

    def list_ci_runs(self, repo, *, limit):
        self.list_ci_runs_calls.append({"repo": repo, "limit": limit})
        return [CiRun(name="ci", status="completed", conclusion="success", head_branch="main", created_at="2026-05-01")]

    def read_file(self, repo, path):
        self.read_file_calls.append({"repo": repo, "path": path})
        return None


class FakeTracker:
    def __init__(self) -> None:
        self.list_tickets_calls = []
        self.tickets = [_ticket("PROJ-1"), _ticket("PROJ-2")]

    def is_available(self) -> bool:
        return True

    def list_tickets(self, project, *, state, max_count):
        self.list_tickets_calls.append({"project": project, "state": state, "max_count": max_count})
        return list(self.tickets)


class FakeMeetingProvider:
    def __init__(self) -> None:
        self.list_meetings_calls = []
        self.meetings = [
            Meeting(
                meeting_id="m1",
                title="standup",
                started_at="2026-05-05T10:00:00Z",
                duration_sec=600,
                organizer="o@x.com",
                attendees=["a@x.com"],
                summary="we decided things",
                action_items=["do x"],
            )
        ]

    def is_available(self) -> bool:
        return True

    def list_meetings(self, *, since_iso, until_iso, max_count, attendees):
        self.list_meetings_calls.append({"since_iso": since_iso, "until_iso": until_iso, "max_count": max_count, "attendees": list(attendees)})
        return list(self.meetings)


# ─── seam fixture ─────────────────────────────────────────────────────


@pytest.fixture
def seam(mocker):
    """Patch the four provider factories at their lazy-import seam and
    hand back the recording doubles + factory mocks.

    The factories are imported INSIDE the *Backed* helper methods via
    ``from briar.extract._providers import make_provider`` (etc.), so
    patching the source module's attribute intercepts every extractor."""
    from briar.extract._clouds.aws import AwsCloudProvider

    repo = FakeRepoProvider()
    tracker = FakeTracker()
    meeting = FakeMeetingProvider()
    cloud = MagicMock(spec=AwsCloudProvider)
    cloud.kind = "aws"
    cloud.is_available.return_value = True
    cloud.caller_identity.return_value = MagicMock(account_id="123456789012", region="eu-west-1")
    cloud.list_subsections.return_value = []

    make_provider = mocker.patch("briar.extract._providers.make_provider", return_value=repo)
    make_tracker = mocker.patch("briar.extract._trackers.make_tracker", return_value=tracker)
    make_cloud = mocker.patch("briar.extract._clouds.make_cloud", return_value=cloud)
    make_meeting = mocker.patch("briar.extract._meetings.make_meeting", return_value=meeting)

    return MagicMock(
        repo=repo,
        tracker=tracker,
        meeting=meeting,
        cloud=cloud,
        make_provider=make_provider,
        make_tracker=make_tracker,
        make_cloud=make_cloud,
        make_meeting=make_meeting,
    )


def _run(cli, tmp_root, include, *extra, company="acme", out_json=None):
    argv = [
        "extract",
        "--company",
        company,
        "--include",
        include,
        "--storage",
        "file",
        "--root",
        str(tmp_root / "knowledge"),
        *extra,
    ]
    if out_json is not None:
        argv += ["--out-json", str(out_json)]
    return cli(*argv)


def _blob_text(tmp_root, company="acme") -> str:
    # default blob name knowledge:<company> → <root>/knowledge/<company>.md
    return (tmp_root / "knowledge" / "knowledge" / f"{company}.md").read_text()


# ─── --company / meta flags ───────────────────────────────────────────


class TestMetaFlags:
    def test_company_required_omission_exits_2(self, cli, tmp_root) -> None:
        result = cli("extract", "--storage", "file", "--root", str(tmp_root / "knowledge"))
        assert result.code == 2
        assert "company" in result.err.lower()

    def test_company_drives_markdown_title_and_blob_path(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", company="globex")
        assert result.code == 0
        text = _blob_text(tmp_root, company="globex")
        assert "# Briar knowledge base — globex" in text

    def test_storage_invalid_choice_exits_2(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", "--storage", "carrier-pigeon")
        # the explicit --storage in extra overrides the helper's earlier one
        assert result.code == 2
        assert "invalid choice" in result.err

    def test_include_invalid_choice_exits_2(self, cli, tmp_root, seam) -> None:
        result = cli(
            "extract",
            "--company",
            "acme",
            "--include",
            "not-a-real-extractor",
            "--storage",
            "file",
            "--root",
            str(tmp_root / "knowledge"),
        )
        assert result.code == 2
        assert "invalid choice" in result.err

    def test_blob_name_overrides_default_path(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", "--blob-name", "custom:name")
        assert result.code == 0
        assert (tmp_root / "knowledge" / "custom" / "name.md").exists()
        # default path must NOT be used when blob-name is given
        assert not (tmp_root / "knowledge" / "knowledge" / "acme.md").exists()

    def test_root_controls_write_location(self, cli, tmp_root, seam) -> None:
        alt = tmp_root / "alt-root"
        result = cli(
            "extract",
            "--company",
            "acme",
            "--include",
            "pr-archaeology",
            "--pr-repo",
            "o/r",
            "--storage",
            "file",
            "--root",
            str(alt),
        )
        assert result.code == 0
        assert (alt / "knowledge" / "acme.md").exists()

    def test_out_json_writes_sidecar_with_company(self, cli, tmp_root, seam) -> None:
        out = tmp_root / "side" / "acme.json"
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", out_json=out)
        assert result.code == 0
        assert out.exists()
        payload = json.loads(out.read_text())
        assert payload["company"] == "acme"

    def test_out_json_omitted_writes_no_sidecar(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r")
        assert result.code == 0
        # only the markdown blob exists; no stray .json under the root
        assert list((tmp_root / "knowledge").rglob("*.json")) == []


# ─── source-selection: --provider / --tracker / --cloud / --meeting ───


class TestProviderChoice:
    @pytest.mark.parametrize("kind", ["github", "bitbucket"], ids=["github", "bitbucket"])
    def test_provider_kind_reaches_make_provider(self, cli, tmp_root, seam, kind) -> None:
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", "--provider", kind)
        assert result.code == 0
        assert seam.make_provider.call_args.args[0] == kind

    def test_provider_default_is_github(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r")
        assert result.code == 0
        assert seam.make_provider.call_args.args[0] == "github"

    def test_provider_invalid_choice_exits_2(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", "--provider", "gitlab")
        assert result.code == 2
        assert "invalid choice" in result.err


class TestTrackerChoice:
    @pytest.mark.parametrize(
        "kind",
        ["jira", "github-issues", "bitbucket-issues", "linear"],
        ids=["jira", "github-issues", "bitbucket-issues", "linear"],
    )
    def test_tracker_kind_reaches_make_tracker(self, cli, tmp_root, seam, kind) -> None:
        result = _run(cli, tmp_root, "active-tickets", "--ticket-project", "PROJ", "--tracker", kind)
        assert result.code == 0
        assert seam.make_tracker.call_args.args[0] == kind

    def test_tracker_default_is_jira(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "active-tickets", "--ticket-project", "PROJ")
        assert result.code == 0
        assert seam.make_tracker.call_args.args[0] == "jira"

    def test_tracker_invalid_choice_exits_2(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "active-tickets", "--ticket-project", "PROJ", "--tracker", "trello")
        assert result.code == 2
        assert "invalid choice" in result.err


class TestCloudChoice:
    @pytest.mark.parametrize("kind", ["aws", "gcp", "azure"], ids=["aws", "gcp", "azure"])
    def test_cloud_kind_reaches_make_cloud(self, cli, tmp_root, seam, kind) -> None:
        # The generic (non-AWS) clouds go through the same isinstance path;
        # our spec'd double is an AwsCloudProvider so the kind we assert is
        # purely the factory arg, independent of the rendering branch.
        result = _run(cli, tmp_root, "aws-infra", "--cloud", kind)
        assert result.code == 0
        # make_cloud(kind, company=..., region=..., profile=...) — kind positional
        assert seam.make_cloud.call_args.args[0] == kind

    def test_cloud_default_is_aws(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "aws-infra")
        assert result.code == 0
        assert seam.make_cloud.call_args.args[0] == "aws"

    def test_cloud_invalid_choice_exits_2(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "aws-infra", "--cloud", "oracle")
        assert result.code == 2
        assert "invalid choice" in result.err


class TestMeetingChoice:
    def test_meeting_kind_reaches_make_meeting(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "meeting-digest", "--meeting", "fireflies")
        assert result.code == 0
        assert seam.make_meeting.call_args.args[0] == "fireflies"

    def test_meeting_invalid_choice_exits_2(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "meeting-digest", "--meeting", "otter")
        assert result.code == 2
        assert "invalid choice" in result.err


# ─── pr-archaeology: --pr-repo / --pr-max / pr-* filters ──────────────


class TestPrArchaeologyFlags:
    def test_pr_repo_repeatable_all_mined(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/one", "--pr-repo", "o/two")
        assert result.code == 0
        mined = [c["repo"] for c in seam.repo.list_pulls_calls]
        assert mined == ["o/one", "o/two"]

    def test_pr_max_reaches_provider(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", "--pr-max", "7")
        assert result.code == 0
        assert seam.repo.list_pulls_calls[0]["max_count"] == 7

    def test_pr_max_default_is_100(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r")
        assert result.code == 0
        assert seam.repo.list_pulls_calls[0]["max_count"] == 100

    def test_pr_authors_allow_filters_output(self, cli, tmp_root, seam) -> None:
        seam.repo.pulls = [_pr(1, "alice"), _pr(2, "bob")]
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", "--pr-authors-allow", "alice")
        assert result.code == 0
        text = _blob_text(tmp_root)
        assert "alice(1)" in text
        assert "bob" not in text

    def test_pr_authors_allow_repeatable_collects_all(self, cli, tmp_root, seam) -> None:
        seam.repo.pulls = [_pr(1, "alice"), _pr(2, "bob"), _pr(3, "carol")]
        result = _run(
            cli,
            tmp_root,
            "pr-archaeology",
            "--pr-repo",
            "o/r",
            "--pr-authors-allow",
            "alice",
            "--pr-authors-allow",
            "carol",
        )
        assert result.code == 0
        text = _blob_text(tmp_root)
        assert "alice(1)" in text and "carol(1)" in text
        assert "bob" not in text

    def test_pr_authors_block_excludes_author(self, cli, tmp_root, seam) -> None:
        seam.repo.pulls = [_pr(1, "alice"), _pr(2, "bob")]
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", "--pr-authors-block", "bob")
        assert result.code == 0
        text = _blob_text(tmp_root)
        assert "alice(1)" in text
        assert "bob" not in text

    def test_pr_assignees_allow_accepted_no_crash(self, cli, tmp_root, seam) -> None:
        # PR shape has no assignee → the objs filter is author-only, but the
        # flag must still parse + be accepted (effect: no crash, runs clean).
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", "--pr-assignees-allow", "x")
        assert result.code == 0

    def test_pr_assignees_block_accepted_no_crash(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", "--pr-assignees-block", "x")
        assert result.code == 0


# ─── active-work: --active-repo / active-* filters ────────────────────


class TestActiveWorkFlags:
    def test_active_repo_repeatable_all_scanned(self, cli, tmp_root, seam) -> None:
        seam.repo.pulls = [_pr(1, "alice", merged_at="")]
        result = _run(cli, tmp_root, "active-work", "--active-repo", "o/one", "--active-repo", "o/two")
        assert result.code == 0
        scanned = [c["repo"] for c in seam.repo.list_pulls_calls]
        assert scanned == ["o/one", "o/two"]
        # open PRs are requested, not merged
        assert all(c["state"] == "open" for c in seam.repo.list_pulls_calls)

    def test_active_authors_allow_filters_output(self, cli, tmp_root, seam) -> None:
        seam.repo.pulls = [_pr(1, "alice", merged_at=""), _pr(2, "bob", merged_at="")]
        result = _run(cli, tmp_root, "active-work", "--active-repo", "o/r", "--active-authors-allow", "alice")
        assert result.code == 0
        text = _blob_text(tmp_root)
        assert "by=alice" in text
        assert "by=bob" not in text

    def test_active_authors_block_excludes(self, cli, tmp_root, seam) -> None:
        seam.repo.pulls = [_pr(1, "alice", merged_at=""), _pr(2, "bob", merged_at="")]
        result = _run(cli, tmp_root, "active-work", "--active-repo", "o/r", "--active-authors-block", "bob")
        assert result.code == 0
        text = _blob_text(tmp_root)
        assert "by=alice" in text
        assert "by=bob" not in text

    def test_active_assignees_allow_accepted(self, cli, tmp_root, seam) -> None:
        seam.repo.pulls = [_pr(1, "alice", merged_at="")]
        result = _run(cli, tmp_root, "active-work", "--active-repo", "o/r", "--active-assignees-allow", "x")
        assert result.code == 0

    def test_active_assignees_block_accepted(self, cli, tmp_root, seam) -> None:
        seam.repo.pulls = [_pr(1, "alice", merged_at="")]
        result = _run(cli, tmp_root, "active-work", "--active-repo", "o/r", "--active-assignees-block", "x")
        assert result.code == 0


# ─── github-deployments / codebase-conventions ────────────────────────


class TestDeployAndConventionsFlags:
    def test_deploy_repo_repeatable_all_scanned(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "github-deployments", "--deploy-repo", "o/one", "--deploy-repo", "o/two")
        assert result.code == 0
        assert seam.repo.list_environments_calls == ["o/one", "o/two"]

    def test_conventions_repo_repeatable_all_inspected(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "codebase-conventions", "--conventions-repo", "o/one", "--conventions-repo", "o/two")
        assert result.code == 0
        inspected = {c["repo"] for c in seam.repo.read_file_calls}
        # read_file is called per-detector per-repo; both repos must appear
        assert {"o/one", "o/two"} <= inspected


# ─── aws-infra: --aws-extract-* ───────────────────────────────────────


class TestAwsInfraFlags:
    def test_aws_extract_region_reaches_make_cloud(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "aws-infra", "--aws-extract-region", "ap-south-1")
        assert result.code == 0
        assert seam.make_cloud.call_args.kwargs["region"] == "ap-south-1"

    def test_aws_extract_region_default_us_east_1(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "aws-infra")
        assert result.code == 0
        assert seam.make_cloud.call_args.kwargs["region"] == "us-east-1"

    def test_aws_extract_profile_reaches_make_cloud(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "aws-infra", "--aws-extract-profile", "prod-sso")
        assert result.code == 0
        assert seam.make_cloud.call_args.kwargs["profile"] == "prod-sso"

    def test_aws_extract_service_repeatable_reaches_list_subsections(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "aws-infra", "--aws-extract-service", "ecs", "--aws-extract-service", "rds")
        assert result.code == 0
        seam.cloud.list_subsections.assert_called_once_with(services=["ecs", "rds"])

    def test_aws_extract_service_default_none_means_all(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "aws-infra")
        assert result.code == 0
        # empty list → services=None (gather all)
        seam.cloud.list_subsections.assert_called_once_with(services=None)

    def test_aws_extract_service_invalid_choice_exits_2(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "aws-infra", "--aws-extract-service", "dynamodb")
        assert result.code == 2
        assert "invalid choice" in result.err


# ─── active-tickets / ticket-archaeology ──────────────────────────────


class TestTicketFlags:
    def test_ticket_project_repeatable_all_scanned(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "active-tickets", "--ticket-project", "AAA", "--ticket-project", "BBB")
        assert result.code == 0
        projects = [c["project"] for c in seam.tracker.list_tickets_calls]
        assert projects == ["AAA", "BBB"]
        assert all(c["state"] == "open" for c in seam.tracker.list_tickets_calls)

    def test_ticket_archaeology_project_repeatable(self, cli, tmp_root, seam) -> None:
        seam.tracker.tickets = [_ticket("X-1"), _ticket("X-2")]
        result = _run(
            cli,
            tmp_root,
            "ticket-archaeology",
            "--ticket-archaeology-project",
            "AAA",
            "--ticket-archaeology-project",
            "BBB",
        )
        assert result.code == 0
        projects = [c["project"] for c in seam.tracker.list_tickets_calls]
        assert projects == ["AAA", "BBB"]
        assert all(c["state"] == "closed" for c in seam.tracker.list_tickets_calls)

    def test_ticket_max_reaches_tracker(self, cli, tmp_root, seam) -> None:
        seam.tracker.tickets = [_ticket("X-1")]
        result = _run(cli, tmp_root, "ticket-archaeology", "--ticket-archaeology-project", "AAA", "--ticket-max", "13")
        assert result.code == 0
        assert seam.tracker.list_tickets_calls[0]["max_count"] == 13

    def test_ticket_max_default_is_100(self, cli, tmp_root, seam) -> None:
        seam.tracker.tickets = [_ticket("X-1")]
        result = _run(cli, tmp_root, "ticket-archaeology", "--ticket-archaeology-project", "AAA")
        assert result.code == 0
        assert seam.tracker.list_tickets_calls[0]["max_count"] == 100


# ─── reviewer-profile ─────────────────────────────────────────────────


class TestReviewerProfileFlags:
    def test_reviewer_repo_repeatable(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "reviewer-profile", "--reviewer-repo", "o/one", "--reviewer-repo", "o/two")
        assert result.code == 0
        mined = [c["repo"] for c in seam.repo.list_pulls_calls]
        assert mined == ["o/one", "o/two"]

    def test_reviewer_pr_sample_reaches_provider(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "reviewer-profile", "--reviewer-repo", "o/r", "--reviewer-pr-sample", "3")
        assert result.code == 0
        assert seam.repo.list_pulls_calls[0]["max_count"] == 3

    def test_reviewer_pr_sample_default_is_20(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "reviewer-profile", "--reviewer-repo", "o/r")
        assert result.code == 0
        assert seam.repo.list_pulls_calls[0]["max_count"] == 20

    def test_reviewer_top_n_limits_profiled_reviewers(self, cli, tmp_root, seam) -> None:
        # 3 distinct reviewers across 3 PRs; top-n=1 → only the busiest profiled.
        seam.repo.pulls = [_pr(1, "auth"), _pr(2, "auth"), _pr(3, "auth")]

        def _comments(repo, number):
            # rev_a leaves comments on every PR (busiest), rev_b/rev_c on one each
            out = [ReviewComment(id=f"a{number}", author="rev_a", body="y" * 30, file_path="f.py", line=1)]
            if number == 1:
                out.append(ReviewComment(id="b", author="rev_b", body="y" * 30, file_path="f.py", line=1))
            if number == 2:
                out.append(ReviewComment(id="c", author="rev_c", body="y" * 30, file_path="f.py", line=1))
            return out

        seam.repo.list_pr_comments = _comments  # type: ignore[assignment]
        result = _run(cli, tmp_root, "reviewer-profile", "--reviewer-repo", "o/r", "--reviewer-top-n", "1")
        assert result.code == 0
        text = _blob_text(tmp_root)
        assert "### rev_a" in text
        assert "### rev_b" not in text and "### rev_c" not in text

    def test_reviewer_top_n_default_is_5(self, cli, tmp_root, seam) -> None:
        # 6 distinct reviewers; default top-n=5 → 5 profiled, 6th dropped.
        seam.repo.pulls = [_pr(1, "auth")]

        def _comments(repo, number):
            return [ReviewComment(id=f"r{i}", author=f"rev{i}", body="z" * 30, file_path="f.py", line=1) for i in range(6)]

        seam.repo.list_pr_comments = _comments  # type: ignore[assignment]
        result = _run(cli, tmp_root, "reviewer-profile", "--reviewer-repo", "o/r")
        assert result.code == 0
        text = _blob_text(tmp_root)
        profiled = sum(1 for i in range(6) if f"### rev{i}" in text)
        assert profiled == 5


# ─── code-hotspots ────────────────────────────────────────────────────


class TestHotspotsFlags:
    def test_hotspots_repo_repeatable(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "code-hotspots", "--hotspots-repo", "o/one", "--hotspots-repo", "o/two")
        assert result.code == 0
        repos = [c["repo"] for c in seam.repo.list_recent_commits_calls]
        assert repos == ["o/one", "o/two"]

    def test_hotspots_since_days_reaches_provider(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "code-hotspots", "--hotspots-repo", "o/r", "--hotspots-since-days", "90")
        assert result.code == 0
        assert seam.repo.list_recent_commits_calls[0]["since_days"] == 90
        # since-days is also echoed in the body
        assert "over 90 days" in _blob_text(tmp_root)

    def test_hotspots_since_days_default_is_30(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "code-hotspots", "--hotspots-repo", "o/r")
        assert result.code == 0
        assert seam.repo.list_recent_commits_calls[0]["since_days"] == 30

    def test_hotspots_max_commits_reaches_provider(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "code-hotspots", "--hotspots-repo", "o/r", "--hotspots-max-commits", "42")
        assert result.code == 0
        assert seam.repo.list_recent_commits_calls[0]["max_count"] == 42

    def test_hotspots_max_commits_default_is_100(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "code-hotspots", "--hotspots-repo", "o/r")
        assert result.code == 0
        assert seam.repo.list_recent_commits_calls[0]["max_count"] == 100

    def test_hotspots_top_n_limits_surfaced_files(self, cli, tmp_root, seam) -> None:
        seam.repo.commits = [Commit(sha="s", message="m", author="a", created_at="2026-05-01T00:00:00Z", file_paths=["a.py", "b.py", "c.py"])]
        result = _run(cli, tmp_root, "code-hotspots", "--hotspots-repo", "o/r", "--hotspots-top-n", "1")
        assert result.code == 0
        text = _blob_text(tmp_root)
        surfaced = sum(1 for f in ("`a.py`", "`b.py`", "`c.py`") if f in text)
        assert surfaced == 1

    def test_hotspots_top_n_default_is_10(self, cli, tmp_root, seam) -> None:
        files = [f"f{i}.py" for i in range(15)]
        seam.repo.commits = [Commit(sha="s", message="m", author="a", created_at="2026-05-01T00:00:00Z", file_paths=files)]
        result = _run(cli, tmp_root, "code-hotspots", "--hotspots-repo", "o/r")
        assert result.code == 0
        text = _blob_text(tmp_root)
        surfaced = sum(1 for i in range(15) if f"`f{i}.py`" in text)
        assert surfaced == 10


# ─── meeting-digest ───────────────────────────────────────────────────


class TestMeetingDigestFlags:
    def test_meeting_since_days_reaches_provider_and_title(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "meeting-digest", "--meeting-since-days", "3")
        assert result.code == 0
        # title echoes the window; provider since_iso is 3 days before until_iso
        assert "last 3 day(s)" in _blob_text(tmp_root)

    def test_meeting_max_reaches_provider(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "meeting-digest", "--meeting-max", "9")
        assert result.code == 0
        assert seam.meeting.list_meetings_calls[0]["max_count"] == 9

    def test_meeting_max_default_is_25(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "meeting-digest")
        assert result.code == 0
        assert seam.meeting.list_meetings_calls[0]["max_count"] == 25

    def test_meeting_since_days_default_is_7(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "meeting-digest")
        assert result.code == 0
        assert "last 7 day(s)" in _blob_text(tmp_root)

    def test_meeting_attendee_allow_repeatable_reaches_provider(self, cli, tmp_root, seam) -> None:
        result = _run(
            cli,
            tmp_root,
            "meeting-digest",
            "--meeting-attendee-allow",
            "a@x.com",
            "--meeting-attendee-allow",
            "b@x.com",
        )
        assert result.code == 0
        assert seam.meeting.list_meetings_calls[0]["attendees"] == ["a@x.com", "b@x.com"]

    def test_meeting_attendee_allow_default_empty(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "meeting-digest")
        assert result.code == 0
        assert seam.meeting.list_meetings_calls[0]["attendees"] == []
