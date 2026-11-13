"""`briar extract`: parametric per-option EFFECT assertions.

Companion to ``test_extract.py`` (which covers the failure modes and the
blob writing). This file pins EVERY extractor option to an observable
effect. The tests still name each option by its 1.x flag; since 2.0
(decision C12) `FlagsAsConfig` writes it into `.briar.toml` under its short
name (decisions E1 to E8 of docs/decisions/2026-11-10-briar-2-extract-config.md)
and the command runs with only `--company`, `--include` and `--repo`, so
every test proves the config path:

* source-selection options (``provider`` / ``tracker`` / ``cloud`` /
  ``meeting``) → assert the factory was invoked with that kind, and
  that an invalid choice exits 1 naming the key.
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
* meta (``--company`` / ``--include`` / ``root``) → assert title and file
  path.

The four provider factories are mocked at the lazy-import seam
(``briar.extract._providers.make_provider`` etc.); the extractor itself
runs for real, so a flag that the extractor silently ignores makes the
assertion FAIL. No network, no SDKs imported at module scope.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Sequence, Tuple
from unittest.mock import MagicMock

import pytest

from briar.commands.extract import ExtractorKeys
from briar.config import load_project_config
from briar.extract import EXTRACTORS
from briar.extract._meeting import Meeting, MeetingPage
from briar.extract.canonical import SHARED_CONCEPTS
from briar.extract._provider import CiRun, Commit, Deployment, Environment, PullRequest, ReviewComment
from briar.extract._tracker import Ticket

# The login check passes: these tests are about what a run does after it (spec 005 PR 3).
pytestmark = pytest.mark.usefixtures("logins_pass")

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

    def list_meetings(self, page: MeetingPage) -> List[Meeting]:
        self.list_meetings_calls.append({"since_iso": page.since_iso, "until_iso": page.until_iso, "max_count": page.size, "skip": page.skip})
        return list(self.meetings[page.skip : page.skip + page.size])


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


class FlagsAsConfig:
    """Moves 1.x `briar extract` flags to where 2.0 reads them (E1 to E8): a repo list
    stays on the command line as `--repo`, a shared concept goes in `[extract]`, every
    other option goes in `[extract.<name>]` under its short name."""

    FLAG_AND_VALUE = 2
    INT_CONCEPTS = ("since_days", "max", "top_n", "sample")
    SHARED = "extract"

    def __init__(self, include: str, flags: Sequence[str]) -> None:
        self._include = include
        self._flags = flags

    def command_line(self) -> List[str]:
        return [token for dest, setting in self._pairs() if self._is_repo(dest) for token in ("--repo", setting)]

    def toml(self) -> str:
        entries = self._entries()
        lines = [f"[{self.SHARED}]", *self._lines(entries, self.SHARED), f"[{self.SHARED}.{self._include}]", *self._lines(entries, self._include)]
        return "\n".join(lines) + "\n"

    def _keys(self) -> ExtractorKeys:
        return ExtractorKeys(EXTRACTORS[self._include])

    def _pairs(self) -> List[Tuple[str, str]]:
        starts = range(0, len(self._flags), self.FLAG_AND_VALUE)
        return [(self._flags[start].removeprefix("--").replace("-", "_"), self._flags[start + 1]) for start in starts]

    def _is_repo(self, dest: str) -> bool:
        return dest == ExtractorKeys.REPO or (dest in self._keys().dests() and not self._keys().short_name(dest))

    def _entries(self) -> Dict[Tuple[str, str], List[object]]:
        entries: Dict[Tuple[str, str], List[object]] = {}
        for dest, setting in self._pairs():
            if not self._is_repo(dest):
                entries.setdefault(self._where(dest), []).append(self._typed(dest, setting))
        return entries

    def _where(self, dest: str) -> Tuple[str, str]:
        if dest in SHARED_CONCEPTS:
            return (self.SHARED, dest)
        return (self._include, self._keys().short_name(dest))

    def _typed(self, dest: str, setting: str) -> object:
        if dest in self.INT_CONCEPTS:
            return int(setting)
        option = self._keys().dests().get(dest)
        return option.type(setting) if option is not None and option.type else setting

    def _many(self, table: str, key: str) -> bool:
        if table == self.SHARED:
            return key not in self.INT_CONCEPTS
        return isinstance(self._keys().options()[key], argparse._AppendAction)

    def _lines(self, entries: Dict[Tuple[str, str], List[object]], table: str) -> List[str]:
        chosen = {key: settings for (where, key), settings in entries.items() if where == table}
        return [f"{key} = {json.dumps(settings if self._many(table, key) else settings[-1])}" for key, settings in chosen.items()]


@pytest.fixture(autouse=True)
def project_in_tmp_root(cli: Callable[..., SimpleNamespace], tmp_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every run happens in `tmp_root` with the real project config reader. Asks for `cli`
    so it runs after it: `cli` stubs the reader."""
    monkeypatch.chdir(tmp_root)
    monkeypatch.setattr("briar.config.load_project_config", load_project_config)


def _run(cli: Callable[..., SimpleNamespace], tmp_root: Path, include: str, *extra: str, company: str = "acme") -> SimpleNamespace:
    config = FlagsAsConfig(include, extra)
    (tmp_root / ".briar.toml").write_text(config.toml())
    return cli("extract", "--company", company, "--include", include, *config.command_line())


def _blob_text(tmp_root, company="acme") -> str:
    # default blob name knowledge:<company> → <root>/knowledge/<company>.md
    return (tmp_root / "knowledge" / "knowledge" / f"{company}.md").read_text()


# ─── --company / meta flags ───────────────────────────────────────────


class TestMetaFlags:
    def test_company_required_omission_exits_2(self, cli, tmp_root) -> None:
        result = cli("extract")
        assert result.code == 2
        assert "company" in result.err.lower()

    def test_company_drives_markdown_title_and_blob_path(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", company="globex")
        assert result.code == 0
        text = _blob_text(tmp_root, company="globex")
        assert "# Briar knowledge base — globex" in text

    USAGE_ERROR = 2

    def test_store_flag_is_gone_exits_2(self, cli: Callable[..., SimpleNamespace], tmp_root: Path, seam: SimpleNamespace) -> None:
        # 2.0 has one knowledge store (decision C11), so `--store` left.
        run = cli("extract", "--company", "acme", "--include", "pr-archaeology", "--store", "file")
        assert run.code == self.USAGE_ERROR
        assert "unrecognized arguments: --store" in run.err

    @pytest.mark.parametrize("flag", ["--pr-repo", "--pr-max", "--root", "--out-json", "--blob-name", "--provider"])
    def test_flag_moved_to_config_exits_2(self, cli: Callable[..., SimpleNamespace], seam: SimpleNamespace, flag: str) -> None:
        # Decisions C12, C13, E13: only --company, --include and --repo are left.
        run = cli("extract", "--company", "acme", "--include", "pr-archaeology", flag, "x")
        assert run.code == self.USAGE_ERROR
        assert f"unrecognized arguments: {flag}" in run.err

    def test_include_invalid_choice_exits_2(self, cli, tmp_root, seam) -> None:
        result = cli(
            "extract",
            "--company",
            "acme",
            "--include",
            "not-a-real-extractor",
        )
        assert result.code == 2
        assert "invalid choice" in result.err

    def test_root_setting_controls_write_location(self, cli: Callable[..., SimpleNamespace], tmp_root: Path, seam: SimpleNamespace) -> None:
        alt = tmp_root / "alt-root"
        (tmp_root / ".briar.toml").write_text(f"root = {json.dumps(str(alt))}\n")
        run = cli("extract", "--company", "acme", "--include", "pr-archaeology", "--repo", "o/r")
        assert run.code == 0, run.err
        assert (alt / "knowledge" / "acme.md").exists()


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

    def test_provider_invalid_choice_exits_1(self, cli: Callable[..., SimpleNamespace], tmp_root: Path, seam: SimpleNamespace) -> None:
        run = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", "--provider", "forgejo")
        assert run.code == 1
        assert "is not one of" in run.err


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

    def test_tracker_invalid_choice_exits_1(self, cli: Callable[..., SimpleNamespace], tmp_root: Path, seam: SimpleNamespace) -> None:
        run = _run(cli, tmp_root, "active-tickets", "--ticket-project", "PROJ", "--tracker", "trello")
        assert run.code == 1
        assert "is not one of" in run.err


class TestCloudChoice:
    @pytest.mark.parametrize("kind", ["aws", "gcp", "azure"], ids=["aws", "gcp", "azure"])
    def test_cloud_kind_reaches_make_cloud(self, cli, tmp_root, seam, kind) -> None:
        # The generic (non-AWS) clouds go through the same isinstance path;
        # our spec'd double is an AwsCloudProvider so the kind we assert is
        # purely the factory arg, independent of the rendering branch.
        result = _run(cli, tmp_root, "aws-infra", "--cloud", kind)
        assert result.code == 0
        # make_cloud(kind, logins, region=...): kind positional
        assert seam.make_cloud.call_args.args[0] == kind

    def test_cloud_default_is_aws(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "aws-infra")
        assert result.code == 0
        assert seam.make_cloud.call_args.args[0] == "aws"

    def test_cloud_invalid_choice_exits_1(self, cli: Callable[..., SimpleNamespace], tmp_root: Path, seam: SimpleNamespace) -> None:
        run = _run(cli, tmp_root, "aws-infra", "--cloud", "oracle")
        assert run.code == 1
        assert "is not one of" in run.err


class TestMeetingChoice:
    def test_meeting_kind_reaches_make_meeting(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "meeting-digest", "--meeting", "fireflies")
        assert result.code == 0
        assert seam.make_meeting.call_args.args[0] == "fireflies"

    def test_meeting_invalid_choice_exits_1(self, cli: Callable[..., SimpleNamespace], tmp_root: Path, seam: SimpleNamespace) -> None:
        run = _run(cli, tmp_root, "meeting-digest", "--meeting", "otter")
        assert run.code == 1
        assert "is not one of" in run.err


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

    def test_pr_assignees_allow_keeps_only_assigned(self, cli, tmp_root, seam) -> None:
        seam.repo.pulls = [_pr(1, "alice", assignees=["x"]), _pr(2, "bob", assignees=["y"])]
        result = _run(cli, tmp_root, "pr-archaeology", "--pr-repo", "o/r", "--pr-assignees-allow", "x")
        assert result.code == 0
        text = _blob_text(tmp_root)
        assert "alice(1)" in text
        assert "bob" not in text

    def test_canonical_assignees_block_drops_assigned(self, cli, tmp_root, seam) -> None:
        seam.repo.pulls = [_pr(1, "alice", assignees=["x"]), _pr(2, "bob", assignees=["y"])]
        result = _run(cli, tmp_root, "pr-archaeology", "--repo", "o/r", "--assignees-block", "x")
        assert result.code == 0
        text = _blob_text(tmp_root)
        assert "bob(1)" in text
        assert "alice" not in text


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

    def test_no_region_lets_each_cloud_pick_its_own(self, cli: Callable[..., SimpleNamespace], tmp_root: Path, seam: SimpleNamespace) -> None:
        """Real run, 2026-11-12: an Azure extract titled itself `region us-east-1`, the AWS default.
        No region now reaches the cloud empty; AWS still uses us-east-1, Azure eastus, GCP us-central1."""
        run = _run(cli, tmp_root, "aws-infra")
        assert run.code == 0
        assert seam.make_cloud.call_args.kwargs["region"] == ""

    def test_the_auth_profile_reaches_make_cloud(self, cli: Callable[..., SimpleNamespace], tmp_root: Path, seam: SimpleNamespace) -> None:
        """I4: the AWS profile is `[auth.aws] profile`, read through the company logins."""
        (tmp_root / ".briar.toml").write_text('root = "knowledge"\n[auth.aws]\nprofile = "prod-sso"\n')

        run = cli("extract", "--company", "acme", "--include", "aws-infra")

        assert run.code == 0
        assert seam.make_cloud.call_args.args[1].aws().profile() == "prod-sso"

    def test_the_extract_profile_key_names_its_new_place(self, cli: Callable[..., SimpleNamespace], tmp_root: Path, seam: SimpleNamespace) -> None:
        (tmp_root / ".briar.toml").write_text('[extract.aws-infra]\nprofile = "prod-sso"\n')

        run = cli("extract", "--company", "acme", "--include", "aws-infra")

        assert run.code == 1
        assert "`extract.aws-infra.profile` in" in run.err
        assert "moved: the AWS profile is `profile` under [auth.aws]" in run.err

    def test_aws_extract_service_repeatable_reaches_list_subsections(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "aws-infra", "--aws-extract-service", "ecs", "--aws-extract-service", "rds")
        assert result.code == 0
        seam.cloud.list_subsections.assert_called_once_with(services=["ecs", "rds"])

    def test_aws_extract_service_default_none_means_all(self, cli, tmp_root, seam) -> None:
        result = _run(cli, tmp_root, "aws-infra")
        assert result.code == 0
        # empty list → services=None (gather all)
        seam.cloud.list_subsections.assert_called_once_with(services=None)

    def test_aws_extract_service_invalid_choice_exits_1(self, cli: Callable[..., SimpleNamespace], tmp_root: Path, seam: SimpleNamespace) -> None:
        run = _run(cli, tmp_root, "aws-infra", "--aws-extract-service", "dynamodb")
        assert run.code == 1
        assert "is not one of" in run.err


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

