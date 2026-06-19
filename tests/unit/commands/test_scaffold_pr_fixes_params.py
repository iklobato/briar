"""Parametric effect-assertions for EVERY flag of `briar scaffold pr-fixes`.

Companion to test_scaffold.py (do not edit that file). `pr-fixes` shares the
composer with `implementation`; the only template-level deltas are its DEFAULTS
(`--archetype pr-fixer`, `--shape one-shot`). This file therefore:

  * pins the pr-fixes-specific defaults (the delta that distinguishes the two
    subcommands — a regression that reset them to the implementation defaults
    must FAIL here), and
  * re-asserts every other flag's effect through the `pr-fixes` subcommand so a
    flag wired only into `implementation` would be caught.

CI-safety: no optional-SDK imports at module scope; placeholder UUIDs only;
order-independent.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

_SECRET_UUID = "11111111-2222-3333-4444-555555555555"
_GH = ["--owner", "alice", "--repo", "widgets"]


def _run(cli, *flags: str) -> Dict[str, Any]:
    result = cli("scaffold", "pr-fixes", "--prefix", "acme", *flags, "-o", "-")
    assert result.code == 0, f"non-zero exit; stderr={result.err}"
    return json.loads(result.out)


def _refs(bundle: Dict[str, Any]) -> List[str]:
    return sorted(t["implementation_ref"] for t in bundle["tools"])


def _source(bundle: Dict[str, Any], kind: str) -> Dict[str, Any]:
    for s in bundle["sources"]:
        if s["kind"] == kind:
            return s
    raise AssertionError(f"no source of kind {kind!r}")


# ─────────────────────────────────────────────────────────────────────────────
# pr-fixes-SPECIFIC defaults (the delta from `implementation`)
# ─────────────────────────────────────────────────────────────────────────────


class TestPrFixesDefaults:
    def test_default_archetype_is_pr_fixer(self, cli) -> None:
        bundle = _run(cli, *_GH)
        assert bundle["agents"][0]["key"] == "acme-pr-fixer"
        assert bundle["agents"][0]["name"] == "acme-pr-fixer"

    def test_default_shape_is_one_shot(self, cli) -> None:
        bundle = _run(cli, *_GH)
        nodes = bundle["workflows"][0]["graph"]["nodes"]
        kinds = [n["kind"] for n in nodes]
        # one-shot: a single agent node, NO human_checkpoint (the implementation
        # default would have inserted one).
        assert kinds == ["agent"]
        assert "human_checkpoint" not in kinds
        assert bundle["workflows"][0]["graph"]["entry"] == "run"

    def test_pr_fixer_keeps_commit_and_open_pr_on_github(self, cli) -> None:
        # pr-fixer tool_filter = (commit, comment_on_issue, open_pr) — substring
        # match. github.commit_files / github.open_pr / github.comment_on_issue
        # all match; nothing is dropped from the github trio.
        bundle = _run(cli, "--source", "github", *_GH)
        refs = _refs(bundle)
        assert "github.commit_files" in refs
        assert "github.open_pr" in refs
        assert "github.comment_on_issue" in refs

    def test_pr_fixer_drops_all_jira_tools(self, cli) -> None:
        # jira refs are `jira.comment` / `jira.transition` / `jira.update_issue`
        # — none contain the substrings commit / comment_on_issue / open_pr, so
        # the pr-fixer filter drops every jira tool.
        bundle = _run(cli, "--source", "jira", "--jira-project", "ENG")
        assert _refs(bundle) == []


# ─────────────────────────────────────────────────────────────────────────────
# --out / -o
# ─────────────────────────────────────────────────────────────────────────────


class TestOut:
    def test_default_dash_to_stdout(self, cli) -> None:
        assert _run(cli, *_GH)["version"] == 1

    def test_out_path_writes_file(self, cli, tmp_root) -> None:
        out = tmp_root / "prfix.json"
        result = cli("scaffold", "pr-fixes", "--prefix", "acme", *_GH, "-o", str(out))
        assert result.code == 0, result.err
        assert out.exists()
        assert result.err.strip() == f"wrote {out}"  # status on stderr
        assert result.out.strip() == ""  # stdout stays clean


# ─────────────────────────────────────────────────────────────────────────────
# --prefix (required)
# ─────────────────────────────────────────────────────────────────────────────


class TestPrefix:
    def test_required_omission_exits_2(self, cli) -> None:
        result = cli("scaffold", "pr-fixes", *_GH, "-o", "-")
        assert result.code == 2
        assert "--prefix" in result.err

    def test_prefix_on_every_key(self, cli) -> None:
        bundle = _run(cli, *_GH)
        assert bundle["llm_models"][0]["key"] == "acme-model"
        assert bundle["workflows"][0]["key"] == "acme-workflow"
        assert _source(bundle, "github")["key"] == "acme-gh-issues"
        for t in bundle["tools"]:
            assert t["key"].startswith("acme-")


# ─────────────────────────────────────────────────────────────────────────────
# --source (choices / repeatable / default)
# ─────────────────────────────────────────────────────────────────────────────

_SOURCE_FIXTURES = {
    "github": (_GH, ["github.comment_on_issue", "github.commit_files", "github.open_pr"]),
    "bitbucket": (
        ["--bitbucket-workspace", "acme", "--bitbucket-repo", "widgets"],
        ["bitbucket.comment_on_issue", "bitbucket.commit_files", "bitbucket.open_pr"],
    ),
    "jira": (["--jira-project", "ENG"], []),  # pr-fixer filter matches no jira ref
    "aws": ([], []),
    "sentry": (
        ["--sentry-org", "acme", "--sentry-project", "backend", "--sentry-secret-id", _SECRET_UUID],
        ["sentry.comment_on_issue"],  # pr-fixer keeps only comment_on_issue for sentry
    ),
}


class TestSourceChoices:
    @pytest.mark.parametrize("kind", sorted(_SOURCE_FIXTURES), ids=sorted(_SOURCE_FIXTURES))
    def test_each_source_choice_emits_source_and_filtered_tools(self, cli, kind) -> None:
        identity, expected_refs = _SOURCE_FIXTURES[kind]
        bundle = _run(cli, "--source", kind, *identity)
        assert [s["kind"] for s in bundle["sources"]] == [kind]
        assert _refs(bundle) == expected_refs

    def test_invalid_source_exits_2(self, cli) -> None:
        result = cli("scaffold", "pr-fixes", "--prefix", "acme", "--source", "gitlab", *_GH, "-o", "-")
        assert result.code == 2

    def test_source_repeatable(self, cli) -> None:
        bundle = _run(cli, "--source", "github", *_GH, "--source", "aws")
        assert sorted(s["kind"] for s in bundle["sources"]) == ["aws", "github"]

    def test_default_source_github(self, cli) -> None:
        assert [s["kind"] for s in _run(cli, *_GH)["sources"]] == ["github"]


# ─────────────────────────────────────────────────────────────────────────────
# --archetype / --shape (override the pr-fixes defaults)
# ─────────────────────────────────────────────────────────────────────────────

_ARCHETYPES = {
    "engineer": ("engineer", "github.commit_files", None),
    "pr-fixer": ("pr-fixer", "github.commit_files", None),
    "pr-ci-fixer": ("pr-ci-fixer", "github.commit_files", None),
    "pr-conflict-resolver": ("pr-conflict-resolver", "github.commit_files", None),
    "triager": ("triager", "github.comment_on_issue", "github.commit_files"),
}


class TestArchetypeAndShape:
    @pytest.mark.parametrize("name", sorted(_ARCHETYPES), ids=sorted(_ARCHETYPES))
    def test_archetype_override(self, cli, name) -> None:
        suffix, keep, drop = _ARCHETYPES[name]
        bundle = _run(cli, "--source", "github", *_GH, "--archetype", name)
        assert bundle["agents"][0]["key"] == f"acme-{suffix}"
        refs = _refs(bundle)
        assert keep in refs
        if drop is not None:
            assert drop not in refs

    def test_invalid_archetype_exits_2(self, cli) -> None:
        result = cli("scaffold", "pr-fixes", "--prefix", "acme", *_GH, "--archetype", "wizard", "-o", "-")
        assert result.code == 2

    @pytest.mark.parametrize(
        "shape,entry,has_checkpoint",
        [
            ("plan-approve-act", "plan", True),
            ("one-shot", "run", False),
            ("triage", "triage", False),
        ],
        ids=["plan-approve-act", "one-shot", "triage"],
    )
    def test_shape_override(self, cli, shape, entry, has_checkpoint) -> None:
        bundle = _run(cli, *_GH, "--shape", shape)
        graph = bundle["workflows"][0]["graph"]
        assert graph["entry"] == entry
        kinds = [n["kind"] for n in graph["nodes"]]
        assert ("human_checkpoint" in kinds) is has_checkpoint

    def test_invalid_shape_exits_2(self, cli) -> None:
        result = cli("scaffold", "pr-fixes", "--prefix", "acme", *_GH, "--shape", "spiral", "-o", "-")
        assert result.code == 2

    def test_archetype_max_iter_flows(self, cli) -> None:
        # pr-fixer.max_iter = 12 (distinct from engineer's 8).
        assert _run(cli, *_GH)["agents"][0]["max_iter"] == 12


# ─────────────────────────────────────────────────────────────────────────────
# --trigger-kind + per-trigger flags
# ─────────────────────────────────────────────────────────────────────────────


class TestTriggers:
    def test_github_webhook_default(self, cli) -> None:
        trig = _run(cli, *_GH)["triggers"][0]
        assert trig["kind"] == "github_webhook"

    def test_bitbucket_webhook(self, cli) -> None:
        bundle = _run(
            cli,
            "--source",
            "bitbucket",
            "--bitbucket-workspace",
            "acme",
            "--bitbucket-repo",
            "widgets",
            "--trigger-kind",
            "bitbucket_webhook",
        )
        assert bundle["triggers"][0]["payload_to_context_mapping"]["issue_number"] == "$.issue.id"

    def test_schedule_cron(self, cli) -> None:
        trig = _run(cli, *_GH, "--trigger-kind", "schedule_cron")["triggers"][0]
        assert trig["kind"] == "schedule"
        assert trig["key"] == "acme-cron"

    def test_manual_emits_no_trigger(self, cli) -> None:
        assert "triggers" not in _run(cli, *_GH, "--trigger-kind", "manual")

    def test_invalid_trigger_exits_2(self, cli) -> None:
        result = cli("scaffold", "pr-fixes", "--prefix", "acme", *_GH, "--trigger-kind", "pager", "-o", "-")
        assert result.code == 2

    def test_schedule_value_and_default(self, cli) -> None:
        custom = _run(cli, *_GH, "--trigger-kind", "schedule_cron", "--schedule", "*/30 * * * *")
        assert custom["triggers"][0]["schedule_cron"] == "*/30 * * * *"
        default = _run(cli, *_GH, "--trigger-kind", "schedule_cron")
        assert default["triggers"][0]["schedule_cron"] == "0 * * * *"

    def test_webhook_events_override_and_default(self, cli) -> None:
        assert _run(cli, *_GH)["triggers"][0]["filter_rules"]["events"] == ["issues.opened", "issues.labeled"]
        override = _run(cli, *_GH, "--webhook-events", "issues.reopened")
        assert override["triggers"][0]["filter_rules"]["events"] == ["issues.reopened"]

    def test_webhook_events_repeatable(self, cli) -> None:
        bundle = _run(cli, *_GH, "--webhook-events", "a", "--webhook-events", "b")
        assert bundle["triggers"][0]["filter_rules"]["events"] == ["a", "b"]

    def test_webhook_labels_override(self, cli) -> None:
        assert "urgent" in _run(cli, *_GH, "--webhook-labels", "urgent")["triggers"][0]["filter_rules"]["labels_any"]

    def test_bitbucket_webhook_events_and_labels(self, cli) -> None:
        base = ["--source", "bitbucket", "--bitbucket-workspace", "acme", "--bitbucket-repo", "widgets", "--trigger-kind", "bitbucket_webhook"]
        default = _run(cli, *base)
        assert default["triggers"][0]["filter_rules"]["events"] == ["issue:created", "issue:updated"]
        override = _run(cli, *base, "--bitbucket-webhook-events", "issue:comment_created", "--bitbucket-webhook-labels", "needs-fix")
        assert override["triggers"][0]["filter_rules"]["events"] == ["issue:comment_created"]
        assert "needs-fix" in override["triggers"][0]["filter_rules"]["labels_any"]


# ─────────────────────────────────────────────────────────────────────────────
# --llm-provider-key / --model
# ─────────────────────────────────────────────────────────────────────────────


class TestLlm:
    def test_model_override_and_default(self, cli) -> None:
        assert _run(cli, *_GH, "--model", "claude-opus-4-1")["llm_models"][0]["name"] == "claude-opus-4-1"
        assert _run(cli, *_GH)["llm_models"][0]["name"] == "claude-sonnet-4-6"

    def test_provider_override_and_default(self, cli) -> None:
        assert _run(cli, *_GH, "--llm-provider-key", "bedrock")["llm_models"][0]["provider_key"] == "bedrock"
        assert _run(cli, *_GH)["llm_models"][0]["provider_key"] == "anthropic"


# ─────────────────────────────────────────────────────────────────────────────
# --auth-mode + per-source secret-id flags
# ─────────────────────────────────────────────────────────────────────────────


class TestAuth:
    def test_oauth_default(self, cli) -> None:
        src = _source(_run(cli, "--source", "github", *_GH), "github")
        assert src["credentials_ref"] is None
        assert src["credential_binding"]["kind"] == "oauth_connection"

    def test_invalid_auth_mode_exits_2(self, cli) -> None:
        result = cli("scaffold", "pr-fixes", "--prefix", "acme", *_GH, "--auth-mode", "saml", "-o", "-")
        assert result.code == 2

    def test_github_secret_id_pat(self, cli) -> None:
        bundle = _run(cli, "--source", "github", *_GH, "--auth-mode", "pat", "--github-secret-id", _SECRET_UUID)
        assert _source(bundle, "github")["credentials_ref"] == _SECRET_UUID

    def test_pat_without_secret_fails(self, cli) -> None:
        result = cli("scaffold", "pr-fixes", "--prefix", "acme", "--source", "github", *_GH, "--auth-mode", "pat", "-o", "-")
        assert result.code != 0

    def test_bitbucket_secret_id(self, cli) -> None:
        bundle = _run(
            cli,
            "--source",
            "bitbucket",
            "--bitbucket-workspace",
            "acme",
            "--bitbucket-repo",
            "widgets",
            "--auth-mode",
            "pat",
            "--bitbucket-secret-id",
            _SECRET_UUID,
        )
        assert _source(bundle, "bitbucket")["credentials_ref"] == _SECRET_UUID

    def test_jira_secret_id(self, cli) -> None:
        bundle = _run(cli, "--source", "jira", "--jira-project", "ENG", "--jira-secret-id", _SECRET_UUID)
        assert _source(bundle, "jira")["credentials_ref"] == _SECRET_UUID

    def test_sentry_secret_id(self, cli) -> None:
        bundle = _run(cli, "--source", "sentry", "--sentry-org", "acme", "--sentry-project", "backend", "--sentry-secret-id", _SECRET_UUID)
        assert _source(bundle, "sentry")["credentials_ref"] == _SECRET_UUID


# ─────────────────────────────────────────────────────────────────────────────
# Per-source config flags (owner/repo, bitbucket, jira, aws, sentry, filters)
# ─────────────────────────────────────────────────────────────────────────────


class TestGithubFlags:
    def test_owner_repo_config(self, cli) -> None:
        src = _source(_run(cli, "--source", "github", "--owner", "octo", "--repo", "demo"), "github")
        assert src["config"]["owner"] == "octo"
        assert src["config"]["repo"] == "octo/demo"

    def test_requires_owner_and_repo(self, cli) -> None:
        result = cli("scaffold", "pr-fixes", "--prefix", "acme", "--source", "github", "--owner", "octo", "-o", "-")
        assert result.code != 0

    @pytest.mark.parametrize(
        "flag,key",
        [
            ("--github-authors-allow", "authors_allow"),
            ("--github-authors-block", "authors_block"),
            ("--github-assignees-allow", "assignees_allow"),
            ("--github-assignees-block", "assignees_block"),
        ],
        ids=["authors_allow", "authors_block", "assignees_allow", "assignees_block"],
    )
    def test_github_filters(self, cli, flag, key) -> None:
        bundle = _run(cli, "--source", "github", *_GH, flag, "x", flag, "y")
        assert _source(bundle, "github")["config"][key] == ["x", "y"]


_BB = ["--source", "bitbucket", "--bitbucket-workspace", "acme", "--bitbucket-repo", "widgets"]


class TestBitbucketFlags:
    def test_workspace_repo_config(self, cli) -> None:
        cfg = _source(_run(cli, *_BB), "bitbucket")["config"]
        assert cfg["workspace"] == "acme"
        assert cfg["repo"] == "acme/widgets"

    def test_requires_workspace_and_repo(self, cli) -> None:
        result = cli("scaffold", "pr-fixes", "--prefix", "acme", "--source", "bitbucket", "--bitbucket-workspace", "acme", "-o", "-")
        assert result.code != 0

    @pytest.mark.parametrize(
        "flag,key",
        [
            ("--bitbucket-authors-allow", "authors_allow"),
            ("--bitbucket-authors-block", "authors_block"),
            ("--bitbucket-assignees-allow", "assignees_allow"),
            ("--bitbucket-assignees-block", "assignees_block"),
        ],
        ids=["authors_allow", "authors_block", "assignees_allow", "assignees_block"],
    )
    def test_bitbucket_filters(self, cli, flag, key) -> None:
        bundle = _run(cli, *_BB, flag, "x", flag, "y")
        assert _source(bundle, "bitbucket")["config"][key] == ["x", "y"]


class TestJiraFlags:
    def test_project_repeatable(self, cli) -> None:
        bundle = _run(cli, "--source", "jira", "--jira-project", "ENG", "--jira-project", "OPS")
        assert _source(bundle, "jira")["config"]["projects"] == ["ENG", "OPS"]

    def test_jql(self, cli) -> None:
        bundle = _run(cli, "--source", "jira", "--jira-project", "ENG", "--jira-jql", "status = Open")
        assert _source(bundle, "jira")["config"]["jql"] == "status = Open"

    @pytest.mark.parametrize(
        "flag,key",
        [
            ("--jira-authors-allow", "authors_allow"),
            ("--jira-authors-block", "authors_block"),
            ("--jira-assignees-allow", "assignees_allow"),
            ("--jira-assignees-block", "assignees_block"),
        ],
        ids=["authors_allow", "authors_block", "assignees_allow", "assignees_block"],
    )
    def test_jira_filters(self, cli, flag, key) -> None:
        bundle = _run(cli, "--source", "jira", "--jira-project", "ENG", flag, "x", flag, "y")
        assert _source(bundle, "jira")["config"][key] == ["x", "y"]


class TestAwsFlags:
    def test_role_arn_and_external_id(self, cli) -> None:
        bundle = _run(cli, "--source", "aws", "--aws-role-arn", "arn:aws:iam::123456789012:role/briar", "--aws-external-id", "ext-9")
        binding = _source(bundle, "aws")["credential_binding"]
        assert binding["role_arn"] == "arn:aws:iam::123456789012:role/briar"
        assert binding["external_id"] == "ext-9"

    def test_region_override_and_default(self, cli) -> None:
        assert _source(_run(cli, "--source", "aws", "--aws-region", "eu-west-2"), "aws")["config"]["region"] == "eu-west-2"
        assert _source(_run(cli, "--source", "aws"), "aws")["config"]["region"] == "us-east-1"

    def test_services_override_and_default(self, cli) -> None:
        assert _source(_run(cli, "--source", "aws", "--aws-services", "s3"), "aws")["config"]["services"] == ["s3"]
        assert _source(_run(cli, "--source", "aws"), "aws")["config"]["services"] == ["ec2", "iam", "logs"]


_SENTRY = ["--source", "sentry", "--sentry-org", "acme", "--sentry-project", "backend", "--sentry-secret-id", _SECRET_UUID]


class TestSentryFlags:
    def test_org_and_projects(self, cli) -> None:
        bundle = _run(
            cli, "--source", "sentry", "--sentry-org", "acme", "--sentry-project", "backend", "--sentry-project", "worker", "--sentry-secret-id", _SECRET_UUID
        )
        cfg = _source(bundle, "sentry")["config"]
        assert cfg["org"] == "acme"
        assert cfg["projects"] == ["backend", "worker"]

    def test_requires_org_and_project(self, cli) -> None:
        result = cli("scaffold", "pr-fixes", "--prefix", "acme", "--source", "sentry", "--sentry-secret-id", _SECRET_UUID, "-o", "-")
        assert result.code != 0

    def test_environment_level_query(self, cli) -> None:
        bundle = _run(cli, *_SENTRY, "--sentry-environment", "prod", "--sentry-level", "error", "--sentry-query", "is:unresolved")
        cfg = _source(bundle, "sentry")["config"]
        assert cfg["environments"] == ["prod"]
        assert cfg["levels"] == ["error"]
        assert cfg["query"] == "is:unresolved"

    def test_requires_secret_id(self, cli) -> None:
        result = cli("scaffold", "pr-fixes", "--prefix", "acme", "--source", "sentry", "--sentry-org", "acme", "--sentry-project", "backend", "-o", "-")
        assert result.code != 0


# ─────────────────────────────────────────────────────────────────────────────
# --company / --knowledge-store (knowledge splice)
# ─────────────────────────────────────────────────────────────────────────────


class _FakeRef:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeStore:
    body = "## Codebase conventions\nRun tests with pytest.\n"

    def list(self, prefix: str = "") -> list:
        return [_FakeRef(f"{prefix}/task-1")]

    def get_many(self, names):
        return {names[0]: self.body}


class TestKnowledgeSplice:
    def test_company_splices_into_system_prompt(self, cli, mocker) -> None:
        mocker.patch("briar.storage.make_store", return_value=_FakeStore())
        bundle = _run(cli, *_GH, "--company", "Acme")
        prompt = bundle["agents"][0]["system_prompt"]
        assert "Gathered knowledge for Acme" in prompt
        assert "Run tests with pytest" in prompt

    def test_no_company_empty_prompt_and_store_untouched(self, cli, mocker) -> None:
        spy = mocker.patch("briar.storage.make_store", return_value=_FakeStore())
        bundle = _run(cli, *_GH)
        assert bundle["agents"][0]["system_prompt"] == ""
        spy.assert_not_called()

    def test_knowledge_store_value_reaches_make_store(self, cli, mocker) -> None:
        spy = mocker.patch("briar.storage.make_store", return_value=_FakeStore())
        _run(cli, *_GH, "--company", "Acme", "--knowledge-store", "file")
        assert spy.call_args.args[0] == "file"

    def test_knowledge_store_default(self, cli, mocker, monkeypatch) -> None:
        monkeypatch.delenv("BRIAR_DATABASE_URL", raising=False)
        spy = mocker.patch("briar.storage.make_store", return_value=_FakeStore())
        _run(cli, *_GH, "--company", "Acme")
        assert spy.call_args.args[0] == "file"
