"""Command-layer tests for `briar agent` (src/briar/commands/agent.py).

Scope: the wiring from parsed argv -> AgentRunConfig -> AgentRunner, plus
dispatch correctness, argument validation, dry-run short-circuit, and the
setup/failure exit codes. The AgentRunner internals are covered elsewhere
(tests/unit/test_runner_dispatch.py, tests/unit/agent/*) — here we only
assert the *command* builds the right config and maps results to exit codes.

Mocking seams:
  * `briar.commands.agent.AgentRunner` — imported at module top, so patch on
    the agent module. We replace it with a recorder that captures the
    `AgentRunConfig` it was constructed with and returns a canned result.
  * `briar.storage.make_store` / `briar.extract._providers.make_provider` —
    lazy-imported inside `_prepare_agent_workdir`, so patch at their source.
  * The task-scoped context fetchers are stubbed via the agent-module's
    own helper methods so no tracker / meeting network call happens.

No real LLM, git, or network. Every external boundary is mocked.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict

import pytest

import briar.commands.agent as agent_mod
from briar.commands._enums import ExitCode

# ─── helpers ────────────────────────────────────────────────────────────


def _result(**over: Any) -> SimpleNamespace:
    """A stand-in for AgentRunResult with the attrs `_finalize_agent_result`
    reads. `cost_summary()` is a method on the real dataclass."""
    base = dict(
        iterations=2,
        stop_reason="end_turn",
        final_text="",
        tool_calls=0,
        commits=[],
        error="",
    )
    base.update(over)
    ns = SimpleNamespace(**base)
    ns.cost_summary = lambda: "in=0 out=0 ≈$0.000"
    return ns


class _RecordingRunner:
    """Replacement for AgentRunner that records the config + returns a
    pre-seeded result. Constructed once per test via `_install`."""

    captured: Dict[str, Any] = {}
    result: SimpleNamespace = _result()

    def __init__(self, config: Any) -> None:
        type(self).captured["config"] = config
        type(self).captured["constructed"] = type(self).captured.get("constructed", 0) + 1

    def run(self) -> SimpleNamespace:
        type(self).captured["ran"] = type(self).captured.get("ran", 0) + 1
        return type(self).result


@pytest.fixture
def agent_seam(mocker: Any):
    """Patch the AgentRunner + store/provider factories + context fetchers.

    Returns a namespace with `runner` (the recording class, so a test can
    read `runner.captured['config']`) and `set_result` to seed the run's
    outcome before invoking the CLI."""

    runner_cls = type("Runner", (_RecordingRunner,), {"captured": {}, "result": _result()})
    mocker.patch.object(agent_mod, "AgentRunner", runner_cls)

    fake_store = mocker.MagicMock(name="store")
    fake_provider = mocker.MagicMock(name="provider")
    fake_provider.kind = "github"
    fake_provider.pr_creation_recipe.return_value = "  6. open a PR\n"
    make_store = mocker.patch("briar.storage.make_store", return_value=fake_store)
    make_provider = mocker.patch("briar.extract._providers.make_provider", return_value=fake_provider)

    # Stub the JIT context fetchers so no tracker / PR / meeting network call
    # fires. They return empty section lists, matching the real "fetch failed
    # → degrade gracefully" contract.
    mocker.patch.object(agent_mod.CommandAgent, "_fetch_ticket_context", staticmethod(lambda **kw: []))
    mocker.patch.object(agent_mod.CommandAgent, "_fetch_pr_context", staticmethod(lambda **kw: []))
    mocker.patch.object(
        agent_mod.CommandAgent,
        "_fetch_meeting_context_from_args",
        lambda self, args, default_query: [],
    )

    ns = SimpleNamespace(
        runner=runner_cls,
        store=fake_store,
        provider=fake_provider,
        make_store=make_store,
        make_provider=make_provider,
    )

    def set_result(**over: Any) -> None:
        runner_cls.result = _result(**over)

    ns.set_result = set_result
    return ns


# Common required flags for `agent implement` so each test only states what's
# under test. Dry-run is on by default so no clone/git happens.
_IMPL = [
    "agent",
    "implement",
    "--company",
    "acme",
    "--owner",
    "acme",
    "--repo",
    "widgets",
    "--ticket-project",
    "acme/widgets",
    "--ticket-key",
    "ENG-7",
    "--dry-run",
]

_PRFIX = [
    "agent",
    "prfix",
    "--company",
    "acme",
    "--owner",
    "acme",
    "--repo",
    "widgets",
    "--pr",
    "42",
    "--branch",
    "feature-x",
    "--dry-run",
]


# ─── happy path: implement ──────────────────────────────────────────────


class TestImplementHappyPath:
    def test_dry_run_builds_engineer_config_and_succeeds(self, cli, agent_seam) -> None:
        result = cli(*_IMPL)
        assert result.code == ExitCode.OK
        cfg = agent_seam.runner.captured["config"]
        # The command must wire the parsed argv into the config verbatim.
        assert cfg.company == "acme"
        assert cfg.task == "implement"
        assert cfg.archetype_name == "engineer"
        assert cfg.target == "acme/widgets"
        assert cfg.dry_run is True
        # The runner was actually driven exactly once.
        assert agent_seam.runner.captured["ran"] == 1

    def test_dry_run_does_not_clone_or_make_provider_token(self, cli, agent_seam, mocker) -> None:
        # In dry-run, _prepare_agent_workdir must skip the clone entirely.
        # If it tried to clone, GitPython's Repo.clone_from would be hit.
        clone = mocker.patch.object(agent_mod.CommandAgent, "_clone")
        cli(*_IMPL)
        clone.assert_not_called()

    def test_final_text_printed(self, cli, agent_seam) -> None:
        agent_seam.set_result(final_text="DONE-ENG-7")
        result = cli(*_IMPL)
        assert result.code == ExitCode.OK
        assert "--- agent final text ---" in result.out
        assert "DONE-ENG-7" in result.out

    def test_commits_printed(self, cli, agent_seam) -> None:
        agent_seam.set_result(commits=["abc123", "def456"])
        result = cli(*_IMPL)
        assert "--- commits: abc123, def456 ---" in result.out

    def test_model_and_max_iter_threaded_into_config(self, cli, agent_seam) -> None:
        cli(*_IMPL, "--model", "claude-test", "--max-iter", "9")
        cfg = agent_seam.runner.captured["config"]
        assert cfg.model == "claude-test"
        assert cfg.max_iterations == 9


# ─── happy path: prfix + dispatch correctness ───────────────────────────


class TestPrfixHappyPathAndDispatch:
    def test_prfix_builds_pr_fixer_config(self, cli, agent_seam) -> None:
        result = cli(*_PRFIX)
        assert result.code == ExitCode.OK
        cfg = agent_seam.runner.captured["config"]
        assert cfg.task == "prfix"
        assert cfg.archetype_name == "pr-fixer"
        # The PR-specific instructions must mention the PR number + branch
        # so a swapped/dropped arg would be caught.
        assert "#42" in cfg.extra_user_instructions
        assert "feature-x" in cfg.extra_user_instructions

    def test_dispatch_routes_prfix_vs_implement(self, cli, agent_seam) -> None:
        # The registry dispatch must pick the op matching argv[1]. A flipped
        # if/elif would route prfix -> implement (engineer) or vice versa.
        cli(*_PRFIX)
        assert agent_seam.runner.captured["config"].archetype_name == "pr-fixer"
        # Reset and run implement; archetype must change.
        agent_seam.runner.captured.clear()
        cli(*_IMPL)
        assert agent_seam.runner.captured["config"].archetype_name == "engineer"


# ─── argument validation ────────────────────────────────────────────────


class TestArgumentValidation:
    def test_missing_required_ticket_key_exits_2(self, cli, agent_seam) -> None:
        argv = [a for a in _IMPL if a not in ("--ticket-key", "ENG-7")]
        result = cli(*argv)
        assert result.code == 2  # argparse usage error
        assert "ticket-key" in result.err

    def test_missing_required_pr_exits_2(self, cli, agent_seam) -> None:
        argv = [a for a in _PRFIX if a not in ("--pr", "42")]
        result = cli(*argv)
        assert result.code == 2
        assert "--pr" in result.err

    def test_invalid_store_choice_exits_2(self, cli, agent_seam) -> None:
        result = cli(*_IMPL, "--store", "sqlite")
        assert result.code == 2
        assert "--store" in result.err

    def test_non_int_pr_exits_2(self, cli, agent_seam) -> None:
        argv = [("99x" if a == "42" else a) for a in _PRFIX]
        result = cli(*argv)
        assert result.code == 2

    def test_unknown_agent_op_exits_2(self, cli, agent_seam) -> None:
        result = cli("agent", "frobnicate")
        assert result.code == 2

    def test_no_op_exits_2(self, cli, agent_seam) -> None:
        # `dest` subparser is required=True.
        result = cli("agent")
        assert result.code == 2


# ─── failure paths ──────────────────────────────────────────────────────


class TestFailurePaths:
    def test_runner_error_maps_to_general_error(self, cli, agent_seam) -> None:
        agent_seam.set_result(error="agent crashed mid-run")
        result = cli(*_IMPL)
        assert result.code == ExitCode.GENERAL_ERROR

    def test_store_open_failure_exits_general_error(self, cli, agent_seam) -> None:
        # make_store raising → _prepare_agent_workdir returns GENERAL_ERROR
        # before any runner is constructed.
        agent_seam.make_store.side_effect = RuntimeError("dsn unreachable")
        result = cli(*_IMPL)
        assert result.code == ExitCode.GENERAL_ERROR
        assert "config" not in agent_seam.runner.captured  # runner never built

    def test_provider_construct_failure_exits_usage_error(self, cli, agent_seam) -> None:
        # make_provider raising → USAGE_ERROR (unknown provider kind is a
        # user-fixable flag mistake).
        from briar.errors import CliError

        agent_seam.make_provider.side_effect = CliError("unknown provider gitlab")
        result = cli(*_IMPL)
        assert result.code == ExitCode.USAGE_ERROR
        assert "config" not in agent_seam.runner.captured

    def test_clone_failure_aborts_with_general_error(self, cli, agent_seam, mocker) -> None:
        # Real (non-dry-run) path: clone fails → GENERAL_ERROR, no runner.
        argv = [a for a in _IMPL if a != "--dry-run"]
        argv += ["--git-user-name", "Bot", "--git-user-email", "bot@example.test"]
        mocker.patch.object(agent_mod.CommandAgent, "_clone", return_value=False)
        result = cli(*argv)
        assert result.code == ExitCode.GENERAL_ERROR
        assert "config" not in agent_seam.runner.captured

    def test_missing_git_identity_short_circuits(self, cli, agent_seam, mocker) -> None:
        # Real path, clone succeeds, but no git identity supplied and no
        # runbook → _resolve_git_identity raises CliError → exit 1, stderr msg.
        argv = [a for a in _IMPL if a != "--dry-run"]
        mocker.patch.object(agent_mod.CommandAgent, "_clone", return_value=True)
        # No ambient git config either, so the result is deterministic
        # regardless of the test machine's git config / CI.
        mocker.patch.object(agent_mod.CommandAgent, "_ambient_git_identity", return_value=("", ""))
        result = cli(*argv)
        assert result.code == ExitCode.GENERAL_ERROR
        assert "git identity not configured" in result.err
        # The LLM was never reached.
        assert "config" not in agent_seam.runner.captured


# ─── dry-run must not call the LLM ───────────────────────────────────────


class TestDryRunSkipsLLM:
    def test_dry_run_passes_flag_so_runner_skips_llm(self, cli, agent_seam) -> None:
        # The command can't itself "not call the LLM" — it delegates to the
        # runner — but it MUST set dry_run=True so the runner short-circuits.
        # A dropped/negated flag here would spend tokens in prod.
        cli(*_IMPL)
        assert agent_seam.runner.captured["config"].dry_run is True

    def test_no_dry_run_flag_means_config_dry_run_false(self, cli, agent_seam, mocker) -> None:
        argv = [a for a in _IMPL if a != "--dry-run"]
        argv += ["--git-user-name", "Bot", "--git-user-email", "bot@example.test"]
        mocker.patch.object(agent_mod.CommandAgent, "_clone", return_value=True)
        mocker.patch.object(agent_mod.CommandAgent, "_set_git_identity", return_value=True)
        cli(*argv)
        assert agent_seam.runner.captured["config"].dry_run is False
