"""Parametric effect-assertions for EVERY flag of `briar scaffold implementation`.

Companion to test_scaffold.py (do not edit that file). Every flag in
/tmp/cli_manifest/scaffold.md is driven to a NON-default value through the
real CLI and the emitted workflow-spec JSON is parsed and asserted to change
accordingly. A flag that the composer silently ignores makes a test FAIL.

CI-safety: no optional-SDK imports at module scope; no real secret literals
(placeholder UUIDs only); order-independent (each test invokes the CLI fresh).
The knowledge-splice flags (--company / --knowledge-store) patch
`briar.storage.make_store` at the import seam — no real store/network.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

# Placeholder secret-shaped values — obvious non-secrets so GitGuardian is happy.
# Shape (a UUID) is realistic; the value is not a real credential.
_SECRET_UUID = "11111111-2222-3333-4444-555555555555"
_OTHER_UUID = "99999999-8888-7777-6666-555555555555"

# Minimal GitHub-source args that make `implementation` build cleanly. The
# default source is github (the template forces it when --source is empty), so
# every happy-path invocation needs an owner+repo.
_GH = ["--owner", "alice", "--repo", "widgets"]


def _run(cli, *flags: str) -> Dict[str, Any]:
    """Invoke `scaffold implementation` with the given flags + a prefix and
    return the parsed JSON bundle. Asserts a clean exit so an ignored flag that
    breaks the build surfaces here, not as a confusing KeyError later."""
    result = cli("scaffold", "implementation", "--prefix", "acme", *flags, "-o", "-")
    assert result.code == 0, f"non-zero exit; stderr={result.err}"
    return json.loads(result.out)


def _refs(bundle: Dict[str, Any]) -> List[str]:
    return sorted(t["implementation_ref"] for t in bundle["tools"])


def _source(bundle: Dict[str, Any], kind: str) -> Dict[str, Any]:
    for s in bundle["sources"]:
        if s["kind"] == kind:
            return s
    raise AssertionError(f"no source of kind {kind!r} in {[s['kind'] for s in bundle['sources']]}")


# ─────────────────────────────────────────────────────────────────────────────
# --out / -o : default stdout vs file path
# ─────────────────────────────────────────────────────────────────────────────


class TestOut:
    def test_default_dash_prints_json_to_stdout(self, cli) -> None:
        bundle = _run(cli, *_GH)  # _run uses -o -
        assert bundle["version"] == 1

    def test_out_path_writes_file_and_prints_confirmation(self, cli, tmp_root) -> None:
        out = tmp_root / "spec.json"
        result = cli("scaffold", "implementation", "--prefix", "acme", *_GH, "-o", str(out))
        assert result.code == 0, result.err
        assert out.exists()
        # Confirmation is a status line on stderr; stdout stays clean when
        # the bundle is written to a path.
        assert result.err.strip() == f"wrote {out}"
        assert result.out.strip() == ""
        assert json.loads(out.read_text())["version"] == 1

    def test_long_form_out_flag_also_writes(self, cli, tmp_root) -> None:
        out = tmp_root / "spec2.json"
        result = cli("scaffold", "implementation", "--prefix", "acme", *_GH, "--out", str(out))
        assert result.code == 0, result.err
        assert out.exists()


# ─────────────────────────────────────────────────────────────────────────────
# --prefix : REQUIRED + prepended to EVERY resource name
# ─────────────────────────────────────────────────────────────────────────────


class TestPrefix:
    def test_required_omission_exits_2_with_message(self, cli) -> None:
        result = cli("scaffold", "implementation", *_GH, "-o", "-")
        assert result.code == 2
        assert "--prefix" in result.err

    def test_prefix_prepended_to_every_resource_key(self, cli) -> None:
        bundle = _run(cli, *_GH)
        # llm_model, source, tools, agent, workflow keys all carry the prefix.
        assert bundle["llm_models"][0]["key"] == "acme-model"
        assert bundle["agents"][0]["key"] == "acme-engineer"
        assert bundle["workflows"][0]["key"] == "acme-workflow"
        assert _source(bundle, "github")["key"] == "acme-gh-issues"
        for t in bundle["tools"]:
            assert t["key"].startswith("acme-")
        assert bundle["agents"][0]["llm_model_key"] == "acme-model"

    def test_distinct_prefix_changes_keys(self, cli) -> None:
        # Mutation guard: a hardcoded prefix would not vary here.
        result = cli("scaffold", "implementation", "--prefix", "zeta", *_GH, "-o", "-")
        bundle = json.loads(result.out)
        assert bundle["workflows"][0]["key"] == "zeta-workflow"
        assert bundle["llm_models"][0]["key"] == "zeta-model"


# ─────────────────────────────────────────────────────────────────────────────
# --source : choices, repeatable, each choice drives its source block + tools
# ─────────────────────────────────────────────────────────────────────────────

# What identity flags each source needs to build, and the tool refs it emits.
_SOURCE_FIXTURES = {
    "github": (
        _GH,
        ["github.comment_on_issue", "github.commit_files", "github.open_pr"],
    ),
    "bitbucket": (
        ["--bitbucket-workspace", "acme", "--bitbucket-repo", "widgets"],
        ["bitbucket.comment_on_issue", "bitbucket.commit_files", "bitbucket.open_pr"],
    ),
    "jira": (
        ["--jira-project", "ENG"],
        ["jira.comment", "jira.transition", "jira.update_issue"],
    ),
    "aws": (
        [],
        [],  # AWS is read-only: contributes a source row but zero tools.
    ),
    "sentry": (
        ["--sentry-org", "acme", "--sentry-project", "backend", "--sentry-secret-id", _SECRET_UUID],
        ["sentry.assign_issue", "sentry.comment_on_issue", "sentry.ignore_issue", "sentry.resolve_issue"],
    ),
}


class TestSourceChoices:
    @pytest.mark.parametrize("kind", sorted(_SOURCE_FIXTURES), ids=sorted(_SOURCE_FIXTURES))
    def test_each_source_choice_emits_its_source_and_tools(self, cli, kind) -> None:
        identity, expected_refs = _SOURCE_FIXTURES[kind]
        bundle = _run(cli, "--source", kind, *identity)
        assert [s["kind"] for s in bundle["sources"]] == [kind]
        assert _refs(bundle) == expected_refs

    def test_invalid_source_choice_exits_2(self, cli) -> None:
        result = cli("scaffold", "implementation", "--prefix", "acme", "--source", "gitlab", *_GH, "-o", "-")
        assert result.code == 2
        assert "gitlab" in result.err or "invalid choice" in result.err

    def test_source_repeatable_collects_all_kinds(self, cli) -> None:
        bundle = _run(
            cli,
            "--source",
            "github",
            *_GH,
            "--source",
            "jira",
            "--jira-project",
            "ENG",
            "--source",
            "aws",
        )
        assert sorted(s["kind"] for s in bundle["sources"]) == ["aws", "github", "jira"]
        # github (3) + jira (3) + aws (0) action tools.
        assert len(bundle["tools"]) == 6

    def test_default_source_is_github_when_omitted(self, cli) -> None:
        # implementation forces github when --source omitted (needs owner/repo).
        bundle = _run(cli, *_GH)
        assert [s["kind"] for s in bundle["sources"]] == ["github"]


# ─────────────────────────────────────────────────────────────────────────────
# --archetype : choices drive persona + tool_filter
# ─────────────────────────────────────────────────────────────────────────────

# Each archetype: the agent-key suffix (== archetype name) and a tool-ref the
# filter must KEEP plus one it must DROP (None when nothing is dropped).
_ARCHETYPES = {
    "engineer": ("engineer", "github.commit_files", None),
    "pr-fixer": ("pr-fixer", "github.commit_files", None),
    "pr-ci-fixer": ("pr-ci-fixer", "github.commit_files", None),
    "pr-conflict-resolver": ("pr-conflict-resolver", "github.commit_files", None),
    "triager": ("triager", "github.comment_on_issue", "github.commit_files"),
}


class TestArchetypeChoices:
    @pytest.mark.parametrize("name", sorted(_ARCHETYPES), ids=sorted(_ARCHETYPES))
    def test_archetype_drives_agent_name_and_tool_filter(self, cli, name) -> None:
        suffix, keep_ref, drop_ref = _ARCHETYPES[name]
        bundle = _run(cli, "--source", "github", *_GH, "--archetype", name)
        agent = bundle["agents"][0]
        assert agent["key"] == f"acme-{suffix}"
        assert agent["name"] == f"acme-{suffix}"
        refs = _refs(bundle)
        assert keep_ref in refs
        if drop_ref is not None:
            assert drop_ref not in refs

    def test_default_archetype_is_engineer(self, cli) -> None:
        bundle = _run(cli, *_GH)
        assert bundle["agents"][0]["key"] == "acme-engineer"

    def test_invalid_archetype_exits_2(self, cli) -> None:
        result = cli("scaffold", "implementation", "--prefix", "acme", *_GH, "--archetype", "wizard", "-o", "-")
        assert result.code == 2

    def test_triager_drops_open_pr_and_commit(self, cli) -> None:
        # Mutation guard for the filter operator: triager keeps comments only.
        bundle = _run(cli, "--source", "github", *_GH, "--archetype", "triager")
        refs = _refs(bundle)
        assert "github.comment_on_issue" in refs
        assert "github.open_pr" not in refs
        assert "github.commit_files" not in refs

    def test_archetype_max_iter_flows_into_agent(self, cli) -> None:
        # engineer.max_iter=8, triager.max_iter=5 — the value must reach agent.
        eng = _run(cli, *_GH, "--archetype", "engineer")["agents"][0]["max_iter"]
        tri = _run(cli, *_GH, "--archetype", "triager")["agents"][0]["max_iter"]
        assert eng == 8
        assert tri == 5


# ─────────────────────────────────────────────────────────────────────────────
# --shape : choices drive the workflow graph node set
# ─────────────────────────────────────────────────────────────────────────────


class TestShapeChoices:
    def test_plan_approve_act_has_human_checkpoint_and_branch(self, cli) -> None:
        bundle = _run(cli, *_GH, "--shape", "plan-approve-act")
        kinds = [n["kind"] for n in bundle["workflows"][0]["graph"]["nodes"]]
        assert "human_checkpoint" in kinds
        assert "branch" in kinds
        assert bundle["workflows"][0]["graph"]["entry"] == "plan"

    def test_one_shot_is_single_agent_node(self, cli) -> None:
        bundle = _run(cli, *_GH, "--shape", "one-shot")
        nodes = bundle["workflows"][0]["graph"]["nodes"]
        kinds = [n["kind"] for n in nodes]
        assert "human_checkpoint" not in kinds
        assert kinds == ["agent"]
        assert bundle["workflows"][0]["graph"]["entry"] == "run"

    def test_triage_shape_single_triage_node(self, cli) -> None:
        bundle = _run(cli, *_GH, "--shape", "triage")
        graph = bundle["workflows"][0]["graph"]
        assert graph["entry"] == "triage"
        assert [n["kind"] for n in graph["nodes"]] == ["agent"]

    def test_default_shape_is_plan_approve_act(self, cli) -> None:
        bundle = _run(cli, *_GH)
        kinds = [n["kind"] for n in bundle["workflows"][0]["graph"]["nodes"]]
        assert "human_checkpoint" in kinds

    def test_invalid_shape_exits_2(self, cli) -> None:
        result = cli("scaffold", "implementation", "--prefix", "acme", *_GH, "--shape", "spiral", "-o", "-")
        assert result.code == 2


# ─────────────────────────────────────────────────────────────────────────────
# --trigger-kind + per-trigger flags (--schedule, webhook events/labels)
# ─────────────────────────────────────────────────────────────────────────────


class TestTriggerKind:
    def test_github_webhook_default_emits_github_trigger(self, cli) -> None:
        bundle = _run(cli, *_GH)  # default trigger-kind
        trig = bundle["triggers"][0]
        assert trig["kind"] == "github_webhook"
        assert trig["payload_to_context_mapping"]["issue_number"] == "$.issue.number"

    def test_bitbucket_webhook_emits_bitbucket_trigger(self, cli) -> None:
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
        trig = bundle["triggers"][0]
        assert trig["kind"] == "bitbucket_webhook"
        # Bitbucket issues key on `id`, not `number`.
        assert trig["payload_to_context_mapping"]["issue_number"] == "$.issue.id"

    def test_schedule_cron_emits_schedule_trigger(self, cli) -> None:
        bundle = _run(cli, *_GH, "--trigger-kind", "schedule_cron")
        trig = bundle["triggers"][0]
        assert trig["kind"] == "schedule"
        assert trig["key"] == "acme-cron"

    def test_manual_trigger_emits_no_trigger_row(self, cli) -> None:
        bundle = _run(cli, *_GH, "--trigger-kind", "manual")
        assert "triggers" not in bundle

    def test_invalid_trigger_kind_exits_2(self, cli) -> None:
        result = cli("scaffold", "implementation", "--prefix", "acme", *_GH, "--trigger-kind", "pager", "-o", "-")
        assert result.code == 2


class TestScheduleFlag:
    def test_schedule_value_flows_into_cron(self, cli) -> None:
        bundle = _run(cli, *_GH, "--trigger-kind", "schedule_cron", "--schedule", "*/15 * * * *")
        assert bundle["triggers"][0]["schedule_cron"] == "*/15 * * * *"

    def test_schedule_default_is_top_of_hour(self, cli) -> None:
        bundle = _run(cli, *_GH, "--trigger-kind", "schedule_cron")
        assert bundle["triggers"][0]["schedule_cron"] == "0 * * * *"


class TestWebhookFlags:
    def test_webhook_events_override_default(self, cli) -> None:
        bundle = _run(cli, *_GH, "--webhook-events", "issues.reopened")
        assert bundle["triggers"][0]["filter_rules"]["events"] == ["issues.reopened"]

    def test_webhook_events_default_when_omitted(self, cli) -> None:
        bundle = _run(cli, *_GH)
        assert bundle["triggers"][0]["filter_rules"]["events"] == ["issues.opened", "issues.labeled"]

    def test_webhook_events_repeatable(self, cli) -> None:
        bundle = _run(cli, *_GH, "--webhook-events", "issues.opened", "--webhook-events", "issues.edited")
        assert bundle["triggers"][0]["filter_rules"]["events"] == ["issues.opened", "issues.edited"]

    def test_webhook_labels_override_and_default(self, cli) -> None:
        default = _run(cli, *_GH)["triggers"][0]["filter_rules"]["labels_any"]
        # default=["briar"]; appending adds to it (argparse append on a default list).
        assert default == ["briar"]
        custom = _run(cli, *_GH, "--webhook-labels", "urgent")["triggers"][0]["filter_rules"]["labels_any"]
        assert "urgent" in custom

    def test_bitbucket_webhook_events_override_default(self, cli) -> None:
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
            "--bitbucket-webhook-events",
            "issue:comment_created",
        )
        assert bundle["triggers"][0]["filter_rules"]["events"] == ["issue:comment_created"]

    def test_bitbucket_webhook_events_default(self, cli) -> None:
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
        assert bundle["triggers"][0]["filter_rules"]["events"] == ["issue:created", "issue:updated"]

    def test_bitbucket_webhook_labels_override(self, cli) -> None:
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
            "--bitbucket-webhook-labels",
            "needs-fix",
        )
        assert "needs-fix" in bundle["triggers"][0]["filter_rules"]["labels_any"]


# ─────────────────────────────────────────────────────────────────────────────
# --llm-provider-key / --model : flow into the llm_models block + agent
# ─────────────────────────────────────────────────────────────────────────────


class TestLlmFlags:
    def test_model_flows_into_llm_block(self, cli) -> None:
        bundle = _run(cli, *_GH, "--model", "claude-opus-4-1")
        llm = bundle["llm_models"][0]
        assert llm["name"] == "claude-opus-4-1"
        assert llm["display_name"] == "claude-opus-4-1"

    def test_model_default(self, cli) -> None:
        bundle = _run(cli, *_GH)
        assert bundle["llm_models"][0]["name"] == "claude-sonnet-4-6"

    def test_provider_key_flows_into_llm_block(self, cli) -> None:
        bundle = _run(cli, *_GH, "--llm-provider-key", "bedrock")
        assert bundle["llm_models"][0]["provider_key"] == "bedrock"

    def test_provider_key_default_anthropic(self, cli) -> None:
        bundle = _run(cli, *_GH)
        assert bundle["llm_models"][0]["provider_key"] == "anthropic"


# ─────────────────────────────────────────────────────────────────────────────
# --auth-mode + per-source --*-secret-id : wire into auth on source + tools
# ─────────────────────────────────────────────────────────────────────────────


class TestAuthMode:
    def test_oauth_default_emits_oauth_binding(self, cli) -> None:
        src = _source(_run(cli, "--source", "github", *_GH), "github")
        assert src["credentials_ref"] is None
        assert src["credential_binding"]["kind"] == "oauth_connection"
        assert src["credential_binding"]["provider"] == "github"

    def test_pat_mode_with_github_secret_id_wires_credentials_ref(self, cli) -> None:
        bundle = _run(cli, "--source", "github", *_GH, "--auth-mode", "pat", "--github-secret-id", _SECRET_UUID)
        src = _source(bundle, "github")
        assert src["credentials_ref"] == _SECRET_UUID
        assert src["credential_binding"] is None
        # The same auth also rides on every emitted tool.
        for t in bundle["tools"]:
            assert t["credentials_ref"] == _SECRET_UUID

    def test_pat_mode_without_secret_id_fails(self, cli) -> None:
        result = cli("scaffold", "implementation", "--prefix", "acme", "--source", "github", *_GH, "--auth-mode", "pat", "-o", "-")
        assert result.code != 0
        assert "secret-id" in result.err or "github-secret-id" in result.err

    def test_invalid_auth_mode_exits_2(self, cli) -> None:
        result = cli("scaffold", "implementation", "--prefix", "acme", *_GH, "--auth-mode", "saml", "-o", "-")
        assert result.code == 2

    def test_bitbucket_secret_id_wires_into_auth(self, cli) -> None:
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

    def test_jira_secret_id_wires_into_auth(self, cli) -> None:
        bundle = _run(cli, "--source", "jira", "--jira-project", "ENG", "--jira-secret-id", _SECRET_UUID)
        assert _source(bundle, "jira")["credentials_ref"] == _SECRET_UUID

    def test_sentry_secret_id_wires_into_auth(self, cli) -> None:
        bundle = _run(
            cli,
            "--source",
            "sentry",
            "--sentry-org",
            "acme",
            "--sentry-project",
            "backend",
            "--sentry-secret-id",
            _SECRET_UUID,
        )
        assert _source(bundle, "sentry")["credentials_ref"] == _SECRET_UUID


# ─────────────────────────────────────────────────────────────────────────────
# GitHub source flags: --owner / --repo / author+assignee allow/block
# ─────────────────────────────────────────────────────────────────────────────


class TestGithubSourceFlags:
    def test_owner_and_repo_populate_config(self, cli) -> None:
        src = _source(_run(cli, "--source", "github", "--owner", "octo", "--repo", "demo"), "github")
        assert src["config"]["owner"] == "octo"
        assert src["config"]["repo"] == "octo/demo"

    def test_github_requires_owner_and_repo(self, cli) -> None:
        # owner present, repo missing → ConfigError surfaced as non-zero exit.
        result = cli("scaffold", "implementation", "--prefix", "acme", "--source", "github", "--owner", "octo", "-o", "-")
        assert result.code != 0
        assert "owner" in result.err.lower() or "repo" in result.err.lower()

    @pytest.mark.parametrize(
        "flag,config_key",
        [
            ("--github-authors-allow", "authors_allow"),
            ("--github-authors-block", "authors_block"),
            ("--github-assignees-allow", "assignees_allow"),
            ("--github-assignees-block", "assignees_block"),
        ],
        ids=["authors_allow", "authors_block", "assignees_allow", "assignees_block"],
    )
    def test_github_filters_collect_into_config(self, cli, flag, config_key) -> None:
        bundle = _run(cli, "--source", "github", *_GH, flag, "carol", flag, "dave")
        assert _source(bundle, "github")["config"][config_key] == ["carol", "dave"]

    def test_github_filters_absent_when_unset(self, cli) -> None:
        cfg = _source(_run(cli, "--source", "github", *_GH), "github")["config"]
        for key in ("authors_allow", "authors_block", "assignees_allow", "assignees_block"):
            assert key not in cfg


# ─────────────────────────────────────────────────────────────────────────────
# Bitbucket source flags
# ─────────────────────────────────────────────────────────────────────────────


_BB = ["--source", "bitbucket", "--bitbucket-workspace", "acme", "--bitbucket-repo", "widgets"]


class TestBitbucketSourceFlags:
    def test_workspace_and_repo_populate_config(self, cli) -> None:
        src = _source(_run(cli, *_BB), "bitbucket")
        assert src["config"]["workspace"] == "acme"
        assert src["config"]["repo"] == "acme/widgets"

    def test_bitbucket_requires_workspace_and_repo(self, cli) -> None:
        result = cli(
            "scaffold",
            "implementation",
            "--prefix",
            "acme",
            "--source",
            "bitbucket",
            "--bitbucket-workspace",
            "acme",
            "-o",
            "-",
        )
        assert result.code != 0

    @pytest.mark.parametrize(
        "flag,config_key",
        [
            ("--bitbucket-authors-allow", "authors_allow"),
            ("--bitbucket-authors-block", "authors_block"),
            ("--bitbucket-assignees-allow", "assignees_allow"),
            ("--bitbucket-assignees-block", "assignees_block"),
        ],
        ids=["authors_allow", "authors_block", "assignees_allow", "assignees_block"],
    )
    def test_bitbucket_filters_collect_into_config(self, cli, flag, config_key) -> None:
        bundle = _run(cli, *_BB, flag, "eve", flag, "frank")
        assert _source(bundle, "bitbucket")["config"][config_key] == ["eve", "frank"]


# ─────────────────────────────────────────────────────────────────────────────
# Jira source flags
# ─────────────────────────────────────────────────────────────────────────────


class TestJiraSourceFlags:
    def test_jira_project_repeatable_into_config(self, cli) -> None:
        bundle = _run(cli, "--source", "jira", "--jira-project", "ENG", "--jira-project", "OPS")
        assert _source(bundle, "jira")["config"]["projects"] == ["ENG", "OPS"]

    def test_jira_jql_flows_into_config(self, cli) -> None:
        bundle = _run(cli, "--source", "jira", "--jira-project", "ENG", "--jira-jql", "status = Open")
        assert _source(bundle, "jira")["config"]["jql"] == "status = Open"

    def test_jira_jql_absent_when_unset(self, cli) -> None:
        cfg = _source(_run(cli, "--source", "jira", "--jira-project", "ENG"), "jira")["config"]
        assert "jql" not in cfg

    @pytest.mark.parametrize(
        "flag,config_key",
        [
            ("--jira-authors-allow", "authors_allow"),
            ("--jira-authors-block", "authors_block"),
            ("--jira-assignees-allow", "assignees_allow"),
            ("--jira-assignees-block", "assignees_block"),
        ],
        ids=["authors_allow", "authors_block", "assignees_allow", "assignees_block"],
    )
    def test_jira_filters_collect_into_config(self, cli, flag, config_key) -> None:
        bundle = _run(cli, "--source", "jira", "--jira-project", "ENG", flag, "grace", flag, "heidi")
        assert _source(bundle, "jira")["config"][config_key] == ["grace", "heidi"]


# ─────────────────────────────────────────────────────────────────────────────
# AWS source flags
# ─────────────────────────────────────────────────────────────────────────────


class TestAwsSourceFlags:
    def test_aws_role_arn_and_external_id_in_binding(self, cli) -> None:
        bundle = _run(
            cli,
            "--source",
            "aws",
            "--aws-role-arn",
            "arn:aws:iam::123456789012:role/briar",
            "--aws-external-id",
            "ext-123",
        )
        binding = _source(bundle, "aws")["credential_binding"]
        assert binding["role_arn"] == "arn:aws:iam::123456789012:role/briar"
        assert binding["external_id"] == "ext-123"
        assert binding["kind"] == "aws_role_chain"

    def test_aws_region_flows_into_config(self, cli) -> None:
        bundle = _run(cli, "--source", "aws", "--aws-region", "eu-west-2")
        assert _source(bundle, "aws")["config"]["region"] == "eu-west-2"

    def test_aws_region_default(self, cli) -> None:
        bundle = _run(cli, "--source", "aws")
        assert _source(bundle, "aws")["config"]["region"] == "us-east-1"

    def test_aws_services_repeatable_into_config(self, cli) -> None:
        bundle = _run(cli, "--source", "aws", "--aws-services", "s3", "--aws-services", "logs")
        assert _source(bundle, "aws")["config"]["services"] == ["s3", "logs"]

    def test_aws_services_default_when_omitted(self, cli) -> None:
        bundle = _run(cli, "--source", "aws")
        assert _source(bundle, "aws")["config"]["services"] == ["ec2", "iam", "logs"]

    def test_aws_binding_omits_role_when_unset(self, cli) -> None:
        binding = _source(_run(cli, "--source", "aws"), "aws")["credential_binding"]
        assert "role_arn" not in binding
        assert "external_id" not in binding


# ─────────────────────────────────────────────────────────────────────────────
# Sentry source flags
# ─────────────────────────────────────────────────────────────────────────────


_SENTRY = ["--source", "sentry", "--sentry-org", "acme", "--sentry-project", "backend", "--sentry-secret-id", _SECRET_UUID]


class TestSentrySourceFlags:
    def test_org_and_projects_into_config(self, cli) -> None:
        bundle = _run(
            cli, "--source", "sentry", "--sentry-org", "acme", "--sentry-project", "backend", "--sentry-project", "worker", "--sentry-secret-id", _SECRET_UUID
        )
        cfg = _source(bundle, "sentry")["config"]
        assert cfg["org"] == "acme"
        assert cfg["projects"] == ["backend", "worker"]

    def test_sentry_requires_org_and_project(self, cli) -> None:
        result = cli("scaffold", "implementation", "--prefix", "acme", "--source", "sentry", "--sentry-secret-id", _SECRET_UUID, "-o", "-")
        assert result.code != 0

    def test_sentry_environment_into_config(self, cli) -> None:
        bundle = _run(cli, *_SENTRY, "--sentry-environment", "prod", "--sentry-environment", "staging")
        assert _source(bundle, "sentry")["config"]["environments"] == ["prod", "staging"]

    def test_sentry_level_into_config(self, cli) -> None:
        bundle = _run(cli, *_SENTRY, "--sentry-level", "error", "--sentry-level", "fatal")
        assert _source(bundle, "sentry")["config"]["levels"] == ["error", "fatal"]

    def test_sentry_query_into_config(self, cli) -> None:
        bundle = _run(cli, *_SENTRY, "--sentry-query", "is:unresolved level:error")
        assert _source(bundle, "sentry")["config"]["query"] == "is:unresolved level:error"

    def test_sentry_optional_filters_absent_when_unset(self, cli) -> None:
        cfg = _source(_run(cli, *_SENTRY), "sentry")["config"]
        for key in ("environments", "levels", "query"):
            assert key not in cfg

    def test_sentry_secret_id_required(self, cli) -> None:
        result = cli("scaffold", "implementation", "--prefix", "acme", "--source", "sentry", "--sentry-org", "acme", "--sentry-project", "backend", "-o", "-")
        assert result.code != 0


# ─────────────────────────────────────────────────────────────────────────────
# --company / --knowledge-store : knowledge splice into agent.system_prompt
# Patches `briar.storage.make_store` at the import seam — no real store.
# ─────────────────────────────────────────────────────────────────────────────


class _FakeRef:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeStore:
    """Returns one knowledge blob whose `## Codebase conventions` heading maps
    to the `codebase-conventions` extractor the engineer archetype consumes."""

    def __init__(self) -> None:
        self.body = "## Codebase conventions\n" "Run tests with pytest. Lint with ruff.\n"

    def list(self, prefix: str = "") -> list:
        return [_FakeRef(f"{prefix}/task-1")]

    def get_many(self, names):
        return {names[0]: self.body}


class TestKnowledgeSpliceFlags:
    def test_company_splices_knowledge_into_system_prompt(self, cli, mocker) -> None:
        store = _FakeStore()
        mocker.patch("briar.storage.make_store", return_value=store)
        bundle = _run(cli, *_GH, "--company", "Acme")
        prompt = bundle["agents"][0]["system_prompt"]
        assert "Gathered knowledge for Acme" in prompt
        assert "Run tests with pytest" in prompt

    def test_no_company_leaves_system_prompt_empty(self, cli, mocker) -> None:
        # make_store must NOT even be consulted without --company.
        spy = mocker.patch("briar.storage.make_store", return_value=_FakeStore())
        bundle = _run(cli, *_GH)
        assert bundle["agents"][0]["system_prompt"] == ""
        spy.assert_not_called()

    def test_knowledge_store_value_reaches_make_store(self, cli, mocker) -> None:
        spy = mocker.patch("briar.storage.make_store", return_value=_FakeStore())
        _run(cli, *_GH, "--company", "Acme", "--knowledge-store", "file")
        assert spy.call_args.args[0] == "file"

    def test_knowledge_store_default_when_unset(self, cli, mocker, monkeypatch) -> None:
        # No BRIAR_DATABASE_URL → defaults to the file backend.
        monkeypatch.delenv("BRIAR_DATABASE_URL", raising=False)
        spy = mocker.patch("briar.storage.make_store", return_value=_FakeStore())
        _run(cli, *_GH, "--company", "Acme")
        assert spy.call_args.args[0] == "file"


# ─────────────────────────────────────────────────────────────────────────────
# Cross-cutting: --owner/--repo target interpolation reaches the backstory
# ─────────────────────────────────────────────────────────────────────────────


class TestTargetInterpolation:
    def test_owner_repo_target_in_agent_role_and_backstory(self, cli) -> None:
        bundle = _run(cli, "--source", "github", "--owner", "octo", "--repo", "demo")
        agent = bundle["agents"][0]
        assert "octo/demo" in agent["role"]
        assert "octo/demo" in agent["backstory"]
