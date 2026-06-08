"""Parametric flag-effect tests for `briar agent` (prfix + implement).

Companion to ``test_agent_cmd.py`` — that file pins dispatch + happy/failure
paths; THIS file asserts the EFFECT of every documented flag in
``/tmp/cli_manifest/agent.md``. For each flag we set a non-default value and
prove it reaches the seam it controls:

  * config-bound flags (``--company``/``--model``/``--max-iter``/``--dry-run``)
    → the ``AgentRunConfig`` the (mocked) ``AgentRunner`` is constructed with.
  * setup-bound flags (``--store``/``--knowledge``/``--provider``) → the
    ``make_store`` / ``make_provider`` call arguments.
  * context-bound flags (``--ticket-*``/``--tracker``/``--meeting-*``/``--pr``)
    → the kwargs/args the JIT context fetchers receive (we record them instead
    of stubbing to ``[]`` so the value is observable).
  * identity flags (``--git-user-name``/``--git-user-email``) → the worktree
    git-config writes (non-dry-run path).
  * ``--keep-worktree`` → whether the worktree dir survives cleanup.
  * ``--runbook`` → the ``_load_messages_block`` read, surfaced via config.messages.
  * choices / required / type flags → argparse exit-2 behaviour.

CI-safety: no optional SDK imported at module scope. The AgentRunner is replaced
at the agent-module seam; no real LLM, git, network, or clone. ``--dry-run`` is
used wherever the clone path is irrelevant so no GitPython call fires.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import briar.commands.agent as agent_mod
from briar.commands._enums import ExitCode

# ─── recording seam ─────────────────────────────────────────────────────


def _result(**over: Any) -> SimpleNamespace:
    base = dict(iterations=1, stop_reason="end_turn", final_text="", tool_calls=0, commits=[], error="")
    base.update(over)
    ns = SimpleNamespace(**base)
    ns.cost_summary = lambda: "in=0 out=0 ≈$0.000"
    return ns


@pytest.fixture
def seam(mocker: Any):
    """Record every flag-driven seam call so a test can assert exact values.

    Unlike ``test_agent_cmd.py``'s ``agent_seam`` (which stubs the context
    fetchers to ``[]``), this fixture *captures* the kwargs/args they receive
    so meeting / ticket / pr / provider flags become observable. The
    AgentRunner is a recorder that captures its ``AgentRunConfig``."""

    rec: Dict[str, Any] = {
        "config": None,
        "ran": 0,
        "make_store": None,
        "make_provider": None,
        "ticket_kw": None,
        "pr_kw": None,
        "meeting": None,
        "messages": {},
        "git_writes": [],
    }

    class Runner:
        def __init__(self, config: Any) -> None:
            rec["config"] = config

        def run(self) -> SimpleNamespace:
            rec["ran"] += 1
            return rec["result"]

    rec["result"] = _result()
    mocker.patch.object(agent_mod, "AgentRunner", Runner)

    fake_store = mocker.MagicMock(name="store")
    fake_provider = mocker.MagicMock(name="provider")
    fake_provider.kind = "github"
    fake_provider.pr_creation_recipe.return_value = "  6. open a PR\n"

    def _make_store(kind, *, file_root):
        rec["make_store"] = SimpleNamespace(kind=kind, file_root=file_root)
        return fake_store

    def _make_provider(kind, *, company):
        rec["make_provider"] = SimpleNamespace(kind=kind, company=company)
        return fake_provider

    mocker.patch("briar.storage.make_store", side_effect=_make_store)
    mocker.patch("briar.extract._providers.make_provider", side_effect=_make_provider)

    # Record-not-stub the context fetchers. Returning [] keeps the run cheap
    # while still exposing the args each flag flows into.
    def _ticket(*, company, tracker, ticket_project, ticket_key) -> List[Any]:
        rec["ticket_kw"] = dict(company=company, tracker=tracker, ticket_project=ticket_project, ticket_key=ticket_key)
        return []

    def _pr(*, company, provider, owner, repo, pr) -> List[Any]:
        rec["pr_kw"] = dict(company=company, provider=provider, owner=owner, repo=repo, pr=pr)
        return []

    mocker.patch.object(agent_mod.CommandAgent, "_fetch_ticket_context", staticmethod(_ticket))
    mocker.patch.object(agent_mod.CommandAgent, "_fetch_pr_context", staticmethod(_pr))

    # Capture the meeting args from the real arg-reader, then short-circuit the
    # extractor so no network call fires.
    def _meeting(self, args, default_query) -> List[Any]:
        rec["meeting"] = SimpleNamespace(
            meeting=getattr(args, "meeting", None),
            meeting_key=getattr(args, "meeting_key", None),
            meeting_query=getattr(args, "meeting_query", None),
            meeting_top_k=getattr(args, "meeting_top_k", None),
            meeting_max_bytes=getattr(args, "meeting_max_bytes", None),
            default_query=default_query,
        )
        return []

    mocker.patch.object(agent_mod.CommandAgent, "_fetch_meeting_context_from_args", _meeting)

    # messages block: surface whatever _load_messages_block produces.
    def _messages(args) -> Dict[str, Any]:
        return rec["messages"]

    mocker.patch.object(agent_mod.CommandAgent, "_load_messages_block", staticmethod(_messages))

    return SimpleNamespace(rec=rec, provider=fake_provider, store=fake_store)


# Base argv: dry-run on so clone/git are skipped unless a test opts out.
def _impl(*extra: str) -> List[str]:
    return [
        "agent",
        "implement",
        "--company",
        "acme",
        "--owner",
        "octo",
        "--repo",
        "widgets",
        "--ticket-project",
        "ENG",
        "--ticket-key",
        "ENG-7",
        "--dry-run",
        *extra,
    ]


def _prfix(*extra: str) -> List[str]:
    return [
        "agent",
        "prfix",
        "--company",
        "acme",
        "--owner",
        "octo",
        "--repo",
        "widgets",
        "--pr",
        "42",
        "--branch",
        "feature-x",
        "--dry-run",
        *extra,
    ]


# ─── common flags: shared verbatim by both ops ──────────────────────────


class TestCommonConfigFlags:
    @pytest.mark.parametrize("argv_factory, op", [(_impl, "implement"), (_prfix, "prfix")], ids=["implement", "prfix"])
    def test_company_reaches_config(self, cli, seam, argv_factory, op) -> None:
        cli(*argv_factory("--company", "globex"))
        assert seam.rec["config"].company == "globex"
        # company also flows to provider construction.
        assert seam.rec["make_provider"].company == "globex"

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_owner_repo_reach_target(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory("--owner", "globex", "--repo", "gadgets"))
        # target is composed owner/repo — a swapped pair would fail this.
        assert seam.rec["config"].target == "globex/gadgets"

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_provider_default_is_github(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory())
        assert seam.rec["make_provider"].kind == "github"

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_provider_override_reaches_make_provider(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory("--provider", "bitbucket"))
        assert seam.rec["make_provider"].kind == "bitbucket"

    @pytest.mark.parametrize("store_kind", ["file", "postgres"], ids=["file", "postgres"])
    def test_store_choice_reaches_make_store(self, cli, seam, store_kind) -> None:
        cli(*_impl("--store", store_kind))
        assert seam.rec["make_store"].kind == store_kind

    def test_store_default_is_postgres(self, cli, seam) -> None:
        cli(*_impl())
        assert seam.rec["make_store"].kind == "postgres"

    def test_invalid_store_choice_exits_2(self, cli, seam) -> None:
        result = cli(*_impl("--store", "sqlite"))
        assert result.code == 2
        assert "--store" in result.err

    def test_knowledge_default_is_local_dir(self, cli, seam) -> None:
        cli(*_impl())
        assert str(seam.rec["make_store"].file_root) == "knowledge"

    def test_knowledge_override_reaches_make_store_file_root(self, cli, seam) -> None:
        cli(*_impl("--knowledge", "/tmp/kb-root"))
        assert str(seam.rec["make_store"].file_root) == "/tmp/kb-root"

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_model_default_is_empty_string(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory())
        assert seam.rec["config"].model == ""

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_model_override_reaches_config(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory("--model", "claude-3-5-sonnet-test"))
        assert seam.rec["config"].model == "claude-3-5-sonnet-test"

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_max_iter_default_zero_passed_through(self, cli, seam, argv_factory) -> None:
        # Command passes max_iter verbatim; default is 0 (runner substitutes its own).
        cli(*argv_factory())
        assert seam.rec["config"].max_iterations == 0

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_max_iter_override_reaches_config(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory("--max-iter", "13"))
        assert seam.rec["config"].max_iterations == 13

    def test_max_iter_non_int_exits_2(self, cli, seam) -> None:
        result = cli(*_impl("--max-iter", "lots"))
        assert result.code == 2


# ─── git identity + keep-worktree (non-dry-run path) ────────────────────


class TestGitIdentityAndWorktree:
    def _real_run(self, cli, seam, mocker, argv, *, identity_capture=True):
        """Run the non-dry-run path with clone stubbed True so git-config +
        cleanup are exercised without touching the network or filesystem repo."""
        mocker.patch.object(agent_mod.CommandAgent, "_clone", return_value=True)

        def _set_identity(worktree, name, email) -> bool:
            seam.rec["git_writes"].append((name, email))
            return True

        if identity_capture:
            mocker.patch.object(agent_mod.CommandAgent, "_set_git_identity", staticmethod(_set_identity))
        return cli(*argv)

    def test_git_user_name_and_email_reach_config_writes(self, cli, seam, mocker) -> None:
        argv = [a for a in _impl() if a != "--dry-run"]
        argv += ["--git-user-name", "Briar Bot", "--git-user-email", "bot@example.test"]
        result = self._real_run(cli, seam, mocker, argv)
        assert result.code == ExitCode.OK
        assert seam.rec["git_writes"] == [("Briar Bot", "bot@example.test")]

    def test_missing_git_identity_errors_when_no_flags(self, cli, seam, mocker) -> None:
        # No --git-user-* and no runbook → _resolve_git_identity raises CliError.
        argv = [a for a in _impl() if a != "--dry-run"]
        result = self._real_run(cli, seam, mocker, argv, identity_capture=False)
        assert result.code == ExitCode.GENERAL_ERROR
        assert "git identity not configured" in result.err
        assert seam.rec["config"] is None  # runner never constructed

    def test_keep_worktree_preserves_dir(self, cli, seam, mocker) -> None:
        rmtree = mocker.patch("briar.commands.agent.shutil.rmtree")
        argv = [a for a in _impl() if a != "--dry-run"]
        argv += ["--git-user-name", "Bot", "--git-user-email", "b@x.test", "--keep-worktree"]
        self._real_run(cli, seam, mocker, argv)
        rmtree.assert_not_called()

    def test_without_keep_worktree_dir_is_removed(self, cli, seam, mocker) -> None:
        rmtree = mocker.patch("briar.commands.agent.shutil.rmtree")
        argv = [a for a in _impl() if a != "--dry-run"]
        argv += ["--git-user-name", "Bot", "--git-user-email", "b@x.test"]
        self._real_run(cli, seam, mocker, argv)
        rmtree.assert_called_once()


# ─── dry-run flag ───────────────────────────────────────────────────────


class TestDryRunFlag:
    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_dry_run_sets_config_flag_and_skips_clone(self, cli, seam, mocker, argv_factory) -> None:
        clone = mocker.patch.object(agent_mod.CommandAgent, "_clone")
        cli(*argv_factory())
        assert seam.rec["config"].dry_run is True
        clone.assert_not_called()  # dry-run must not clone

    def test_without_dry_run_config_flag_false(self, cli, seam, mocker) -> None:
        mocker.patch.object(agent_mod.CommandAgent, "_clone", return_value=True)
        mocker.patch.object(agent_mod.CommandAgent, "_set_git_identity", return_value=True)
        argv = [a for a in _impl() if a != "--dry-run"]
        argv += ["--git-user-name", "Bot", "--git-user-email", "b@x.test"]
        cli(*argv)
        assert seam.rec["config"].dry_run is False


# ─── runbook flag → messages block ──────────────────────────────────────


class TestRunbookFlag:
    def test_runbook_messages_reach_config(self, cli, seam) -> None:
        # _load_messages_block is patched to return rec["messages"]; seed it
        # to prove the config carries whatever the runbook produced.
        seam.rec["messages"] = {"eng": {"kind": "slack"}}
        cli(*_impl("--runbook", "/tmp/runbook.yaml"))
        assert seam.rec["config"].messages == {"eng": {"kind": "slack"}}

    def test_no_runbook_means_empty_messages(self, cli, seam) -> None:
        # Default: no messages bound.
        cli(*_impl())
        assert seam.rec["config"].messages == {}


# ─── implement-only flags ───────────────────────────────────────────────


class TestImplementSpecificFlags:
    def test_ticket_project_and_key_reach_ticket_context(self, cli, seam) -> None:
        cli(*_impl("--ticket-project", "PLATFORM", "--ticket-key", "PLATFORM-99"))
        assert seam.rec["ticket_kw"]["ticket_project"] == "PLATFORM"
        assert seam.rec["ticket_kw"]["ticket_key"] == "PLATFORM-99"

    def test_tracker_default_is_jira(self, cli, seam) -> None:
        cli(*_impl())
        assert seam.rec["ticket_kw"]["tracker"] == "jira"

    @pytest.mark.parametrize("tracker", ["jira", "github-issues", "bitbucket-issues", "linear"])
    def test_tracker_override_reaches_ticket_context(self, cli, seam, tracker) -> None:
        cli(*_impl("--tracker", tracker))
        assert seam.rec["ticket_kw"]["tracker"] == tracker

    def test_ticket_key_default_meeting_query(self, cli, seam) -> None:
        # implement's default meeting query is the ticket key.
        cli(*_impl("--ticket-key", "ENG-77"))
        assert seam.rec["meeting"].default_query == "ENG-77"

    def test_missing_ticket_project_exits_2(self, cli, seam) -> None:
        argv = [a for a in _impl() if a not in ("--ticket-project", "ENG")]
        result = cli(*argv)
        assert result.code == 2
        assert "ticket-project" in result.err

    def test_missing_ticket_key_exits_2(self, cli, seam) -> None:
        argv = [a for a in _impl() if a not in ("--ticket-key", "ENG-7")]
        result = cli(*argv)
        assert result.code == 2
        assert "ticket-key" in result.err

    def test_implement_routes_to_engineer(self, cli, seam) -> None:
        cli(*_impl())
        assert seam.rec["config"].archetype_name == "engineer"
        assert seam.rec["config"].task == "implement"


# ─── prfix-only flags ───────────────────────────────────────────────────


class TestPrfixSpecificFlags:
    def test_pr_reaches_pr_context_and_instructions(self, cli, seam) -> None:
        cli(*_prfix("--pr", "777"))
        assert seam.rec["pr_kw"]["pr"] == 777
        assert "#777" in seam.rec["config"].extra_user_instructions

    def test_branch_reaches_clone_and_instructions(self, cli, seam, mocker) -> None:
        # branch is the clone target for prfix; assert it reaches _clone AND
        # the instructions (dry-run skips clone, so assert via instructions).
        cli(*_prfix("--branch", "hotfix-99"))
        assert "hotfix-99" in seam.rec["config"].extra_user_instructions

    def test_branch_is_clone_branch_in_real_run(self, cli, seam, mocker) -> None:
        captured = {}

        def _clone(provider, owner, repo, dest, *, branch=""):
            captured["branch"] = branch
            return True

        mocker.patch.object(agent_mod.CommandAgent, "_clone", staticmethod(_clone))
        mocker.patch.object(agent_mod.CommandAgent, "_set_git_identity", return_value=True)
        argv = [a for a in _prfix() if a != "--dry-run"]
        argv += ["--branch", "release-1", "--git-user-name", "Bot", "--git-user-email", "b@x.test"]
        cli(*argv)
        assert captured["branch"] == "release-1"

    def test_default_meeting_query_is_pr_identifier(self, cli, seam) -> None:
        cli(*_prfix("--owner", "octo", "--repo", "widgets", "--pr", "42"))
        assert seam.rec["meeting"].default_query == "octo/widgets#42"

    def test_missing_pr_exits_2(self, cli, seam) -> None:
        argv = [a for a in _prfix() if a not in ("--pr", "42")]
        result = cli(*argv)
        assert result.code == 2
        assert "--pr" in result.err

    def test_missing_branch_exits_2(self, cli, seam) -> None:
        argv = [a for a in _prfix() if a not in ("--branch", "feature-x")]
        result = cli(*argv)
        assert result.code == 2
        assert "--branch" in result.err

    def test_non_int_pr_exits_2(self, cli, seam) -> None:
        argv = [("notanumber" if a == "42" else a) for a in _prfix()]
        result = cli(*argv)
        assert result.code == 2

    def test_prfix_routes_to_pr_fixer(self, cli, seam) -> None:
        cli(*_prfix())
        assert seam.rec["config"].archetype_name == "pr-fixer"
        assert seam.rec["config"].task == "prfix"

    def test_provider_reaches_pr_context(self, cli, seam) -> None:
        cli(*_prfix("--provider", "bitbucket"))
        assert seam.rec["pr_kw"]["provider"] == "bitbucket"


# ─── meeting flags (shared) ─────────────────────────────────────────────


class TestMeetingFlags:
    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_meeting_default_provider_fireflies(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory())
        assert seam.rec["meeting"].meeting == "fireflies"

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_meeting_override(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory("--meeting", "zoom"))
        assert seam.rec["meeting"].meeting == "zoom"

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_meeting_key_reaches_fetch(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory("--meeting-key", "FF-abc123"))
        assert seam.rec["meeting"].meeting_key == "FF-abc123"

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_meeting_query_override_wins_over_default(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory("--meeting-query", "billing rewrite"))
        # When set, the explicit query is what the fetcher resolves to.
        assert seam.rec["meeting"].meeting_query == "billing rewrite"

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_meeting_query_default_empty_string(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory())
        # The raw arg is "" — the default-query substitution happens inside the
        # (real) arg-reader; we assert the raw arg here.
        assert seam.rec["meeting"].meeting_query == ""

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_meeting_top_k_default_is_3(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory())
        assert seam.rec["meeting"].meeting_top_k == 3

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_meeting_top_k_override(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory("--meeting-top-k", "9"))
        assert seam.rec["meeting"].meeting_top_k == 9

    def test_meeting_top_k_non_int_exits_2(self, cli, seam) -> None:
        result = cli(*_impl("--meeting-top-k", "many"))
        assert result.code == 2

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_meeting_max_bytes_default_50000(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory())
        assert seam.rec["meeting"].meeting_max_bytes == 50_000

    @pytest.mark.parametrize("argv_factory", [_impl, _prfix], ids=["implement", "prfix"])
    def test_meeting_max_bytes_override(self, cli, seam, argv_factory) -> None:
        cli(*argv_factory("--meeting-max-bytes", "1234"))
        assert seam.rec["meeting"].meeting_max_bytes == 1234

    def test_meeting_max_bytes_non_int_exits_2(self, cli, seam) -> None:
        result = cli(*_impl("--meeting-max-bytes", "big"))
        assert result.code == 2


# ─── required-flag omission (common) ────────────────────────────────────


class TestRequiredCommonFlags:
    @pytest.mark.parametrize(
        "drop, needle",
        [
            (("--company", "acme"), "company"),
            (("--owner", "octo"), "owner"),
            (("--repo", "widgets"), "repo"),
        ],
        ids=["company", "owner", "repo"],
    )
    def test_missing_common_required_exits_2(self, cli, seam, drop, needle) -> None:
        argv = [a for a in _impl() if a not in drop]
        result = cli(*argv)
        assert result.code == 2
        assert needle in result.err
