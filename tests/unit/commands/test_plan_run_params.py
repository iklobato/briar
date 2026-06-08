"""Parametric flag-effect tests for `briar plan run` (28 flags).

`plan run` is the widest surface. Two complementary layers:

  1. PARSE/VALIDATE via the `cli` fixture — choices (--tracker/--provider/
     --llm/--store/--journal-store), required flags (--owner/--repo/--llm,
     plus the --company runtime guard), type coercion (--limit/--max-iter/
     --max-replans/--meeting-top-k/--meeting-max-bytes), and the documented
     exit codes.

  2. EFFECT via `RunOp._build_implement_args` — the adapter that translates a
     run-loop Namespace + one card into the `agent implement` Namespace. Every
     per-card flag (owner, repo, tracker-project, tracker, provider, model,
     max-iter, git-user-*, keep-worktree, dry-run, runbook, knowledge,
     meeting*) must land on the implement Namespace with the right value; an
     ignored/swapped flag makes the assertion FAIL.

Control-flow flags (--limit, --continue-on-failure, --max-replans, --dry-run)
have their loop behaviour pinned in tests/test_plan_run.py; here we add the
arg-plumbing assertions those don't cover.

No network, no real LLM/runner: `make_llm` resolves to an available fake and
`agent.run_implement` is mocked at its seam.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from briar.commands._enums import ExitCode
from briar.commands.plan import RunOp
from briar.plan import ImplementationPlan, PlanCard, save_plan
from briar.storage import make_store

# ─── helpers ────────────────────────────────────────────────────────────


def _plan(*cards: PlanCard, name: str = "demo", company: str = "acme") -> ImplementationPlan:
    return ImplementationPlan(
        name=name,
        board_url="",
        tracker="github-issues",
        project="acme/widgets",
        company=company,
        cards=list(cards),
    )


@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    root = tmp_path / "knowledge"
    root.mkdir()
    return root


@pytest.fixture
def journal_root(tmp_path: Path) -> Path:
    root = tmp_path / "journal"
    root.mkdir()
    return root


def _seed(root: Path, plan: ImplementationPlan) -> None:
    save_plan(make_store("file", file_root=root), plan)


class _FakeLLM:
    kind = "fake"

    def is_available(self) -> bool:
        return True

    def complete(self, *, system, messages, tools, max_tokens):  # pragma: no cover
        raise AssertionError("LLM.complete must not be reached")

    def format_tool_result(self, tool_call_id, output, is_error=False):  # pragma: no cover
        return {}


@pytest.fixture
def fake_llm(mocker: Any) -> "_LLMSpy":
    spy = _LLMSpy()

    def _make(kind, *, model=""):
        spy.kind = kind
        spy.model = model
        return _FakeLLM()

    mocker.patch("briar.commands.plan.make_llm", side_effect=_make)
    return spy


class _LLMSpy:
    kind: str = ""
    model: str = ""


def _required(store_root: Path) -> list[str]:
    """The minimum flags `plan run` needs to get past argparse + the
    --company guard (so we can probe the OTHER flags)."""
    return [
        "--owner",
        "acme",
        "--repo",
        "widgets",
        "--company",
        "acme",
        "--llm",
        "anthropic",
        "--store",
        "file",
        "--root",
        str(store_root),
    ]


# ════════════════════════════════════════════════════════════════════════
# LAYER 1 — parse / validate via the CLI
# ════════════════════════════════════════════════════════════════════════


class TestRunRequiredFlags:
    def test_owner_required_exit_2(self, cli, store_root) -> None:
        result = cli("plan", "run", "demo", "--repo", "widgets", "--company", "acme", "--llm", "anthropic", "--store", "file", "--root", str(store_root))
        assert result.code == 2
        assert "--owner" in result.err

    def test_repo_required_exit_2(self, cli, store_root) -> None:
        result = cli("plan", "run", "demo", "--owner", "acme", "--company", "acme", "--llm", "anthropic", "--store", "file", "--root", str(store_root))
        assert result.code == 2
        assert "--repo" in result.err

    def test_llm_required_exit_2(self, cli, store_root) -> None:
        result = cli("plan", "run", "demo", "--owner", "acme", "--repo", "widgets", "--company", "acme", "--store", "file", "--root", str(store_root))
        assert result.code == 2
        assert "--llm" in result.err

    def test_name_required_exit_2(self, cli, store_root) -> None:
        result = cli("plan", "run", *_required(store_root))
        assert result.code == 2

    def test_company_required_runtime_guard_exit_1(self, cli, store_root, fake_llm) -> None:
        # --company has an empty argparse default but RunOp.run raises CliError
        # when it is blank — a runtime requirement, not an argparse one (exit 1).
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        result = cli("plan", "run", "demo", "--owner", "acme", "--repo", "widgets", "--llm", "anthropic", "--store", "file", "--root", str(store_root))
        assert result.code == ExitCode.GENERAL_ERROR
        assert "--company" in result.err


class TestRunChoiceFlags:
    @pytest.mark.parametrize("tracker", ["bitbucket-issues", "github-issues", "jira", "linear"])
    def test_tracker_choice_accepted(self, cli, store_root, fake_llm, mocker, tracker) -> None:
        # An empty plan completes immediately (no implement call); we only need
        # to prove argparse accepts the choice and the loop runs to OK.
        _seed(store_root, _plan(name="demo"))
        mocker.patch("briar.commands.agent.run_implement", return_value=0)
        result = cli("plan", "run", "demo", "--tracker", tracker, *_required(store_root))
        assert result.code == ExitCode.OK

    def test_tracker_invalid_choice_exit_2(self, cli, store_root) -> None:
        result = cli("plan", "run", "demo", "--tracker", "trello", *_required(store_root))
        assert result.code == 2
        assert "--tracker" in result.err

    @pytest.mark.parametrize("provider", ["bitbucket", "github"])
    def test_provider_choice_accepted(self, cli, store_root, fake_llm, mocker, provider) -> None:
        _seed(store_root, _plan(name="demo"))
        mocker.patch("briar.commands.agent.run_implement", return_value=0)
        result = cli("plan", "run", "demo", "--provider", provider, *_required(store_root))
        assert result.code == ExitCode.OK

    def test_provider_invalid_choice_exit_2(self, cli, store_root) -> None:
        result = cli("plan", "run", "demo", "--provider", "gitlab", *_required(store_root))
        assert result.code == 2
        assert "--provider" in result.err

    @pytest.mark.parametrize("llm", ["anthropic", "openai", "gemini", "bedrock"])
    def test_llm_choice_resolves_provider(self, cli, store_root, fake_llm, mocker, llm) -> None:
        _seed(store_root, _plan(name="demo"))
        mocker.patch("briar.commands.agent.run_implement", return_value=0)
        result = cli(
            "plan", "run", "demo", "--owner", "acme", "--repo", "widgets", "--company", "acme", "--llm", llm, "--store", "file", "--root", str(store_root)
        )
        assert result.code == ExitCode.OK
        assert fake_llm.kind == llm

    def test_llm_invalid_choice_exit_2(self, cli, store_root) -> None:
        result = cli(
            "plan", "run", "demo", "--owner", "acme", "--repo", "widgets", "--company", "acme", "--llm", "wat", "--store", "file", "--root", str(store_root)
        )
        assert result.code == 2
        assert "--llm" in result.err

    def test_store_invalid_choice_exit_2(self, cli, store_root) -> None:
        result = cli(
            "plan",
            "run",
            "demo",
            "--owner",
            "acme",
            "--repo",
            "widgets",
            "--company",
            "acme",
            "--llm",
            "anthropic",
            "--store",
            "redis",
            "--root",
            str(store_root),
        )
        assert result.code == 2
        assert "--store" in result.err

    def test_journal_store_invalid_choice_exit_2(self, cli, store_root) -> None:
        result = cli("plan", "run", "demo", "--journal-store", "postgres", *_required(store_root))
        assert result.code == 2
        assert "--journal-store" in result.err


class TestRunIntFlagsCoerce:
    @pytest.mark.parametrize(
        "flag",
        ["--limit", "--max-iter", "--max-replans", "--meeting-top-k", "--meeting-max-bytes"],
    )
    def test_int_flags_reject_non_int_exit_2(self, cli, store_root, flag) -> None:
        result = cli("plan", "run", "demo", flag, "notanumber", *_required(store_root))
        assert result.code == 2


# ════════════════════════════════════════════════════════════════════════
# LAYER 2 — _build_implement_args adapter: every per-card flag's VALUE
# ════════════════════════════════════════════════════════════════════════


def _run_ns(**overrides) -> argparse.Namespace:
    ns = argparse.Namespace()
    defaults = {
        "name": "demo",
        "limit": 0,
        "continue_on_failure": False,
        "max_replans": 3,
        "company": "acme",
        "owner": "acme",
        "repo": "widgets",
        "tracker_project": "",
        "tracker": "github-issues",
        "provider": "github",
        "llm": "anthropic",
        "model": "",
        "max_iter": 0,
        "git_user_name": "",
        "git_user_email": "",
        "keep_worktree": False,
        "dry_run": False,
        "runbook": "",
        "knowledge": "./knowledge",
        "meeting": "fireflies",
        "meeting_key": "",
        "meeting_query": "",
        "meeting_top_k": 3,
        "meeting_max_bytes": 50_000,
        "store": "file",
        "root": "./knowledge",
        "journal_store": "file",
        "journal_root": "./journal",
        "format": "quiet",
        "verbose": False,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(ns, k, v)
    return ns


class TestBuildImplementArgsMapping:
    """Each run-loop flag must reach the `agent implement` Namespace."""

    def _impl(self, **run_overrides) -> argparse.Namespace:
        args = _run_ns(**run_overrides)
        card = PlanCard(key="A", title="a")
        tracker_project = (args.tracker_project or f"{args.owner}/{args.repo}").strip()
        return RunOp._build_implement_args(args, card, tracker_project)

    def test_owner_maps(self) -> None:
        assert self._impl(owner="globex").owner == "globex"

    def test_repo_maps(self) -> None:
        assert self._impl(repo="gadgets").repo == "gadgets"

    def test_tracker_project_explicit_overrides_default(self) -> None:
        # An explicit --tracker-project wins over the <owner>/<repo> default.
        impl = self._impl(owner="acme", repo="widgets", tracker_project="PROJ-X")
        assert impl.ticket_project == "PROJ-X"

    def test_tracker_project_defaults_to_owner_slash_repo(self) -> None:
        impl = self._impl(owner="acme", repo="widgets", tracker_project="")
        assert impl.ticket_project == "acme/widgets"

    def test_tracker_maps(self) -> None:
        assert self._impl(tracker="jira").tracker == "jira"

    def test_provider_maps(self) -> None:
        assert self._impl(provider="bitbucket").provider == "bitbucket"

    def test_model_maps(self) -> None:
        assert self._impl(model="claude-z").model == "claude-z"

    def test_max_iter_maps(self) -> None:
        assert self._impl(max_iter=9).max_iter == 9

    def test_git_user_name_maps(self) -> None:
        assert self._impl(git_user_name="Botty").git_user_name == "Botty"

    def test_git_user_email_maps(self) -> None:
        assert self._impl(git_user_email="bot@x.dev").git_user_email == "bot@x.dev"

    def test_keep_worktree_true_maps(self) -> None:
        assert self._impl(keep_worktree=True).keep_worktree is True

    def test_keep_worktree_false_maps(self) -> None:
        assert self._impl(keep_worktree=False).keep_worktree is False

    def test_dry_run_true_maps(self) -> None:
        assert self._impl(dry_run=True).dry_run is True

    def test_dry_run_false_maps(self) -> None:
        assert self._impl(dry_run=False).dry_run is False

    def test_runbook_maps(self) -> None:
        assert self._impl(runbook="rb.yaml").runbook == "rb.yaml"

    def test_knowledge_maps(self) -> None:
        # --knowledge is the file-store root for `agent implement` (distinct
        # from --root which is the plan store root).
        assert self._impl(knowledge="/k/root").knowledge == "/k/root"

    def test_store_maps(self) -> None:
        assert self._impl(store="postgres").store == "postgres"

    def test_meeting_maps(self) -> None:
        assert self._impl(meeting="otter").meeting == "otter"

    def test_meeting_key_maps(self) -> None:
        assert self._impl(meeting_key="MK-1").meeting_key == "MK-1"

    def test_meeting_query_maps(self) -> None:
        assert self._impl(meeting_query="standup").meeting_query == "standup"

    def test_meeting_top_k_maps(self) -> None:
        assert self._impl(meeting_top_k=11).meeting_top_k == 11

    def test_meeting_max_bytes_maps(self) -> None:
        assert self._impl(meeting_max_bytes=12345).meeting_max_bytes == 12345

    def test_company_maps(self) -> None:
        assert self._impl(company="globex").company == "globex"

    def test_card_key_becomes_ticket_key(self) -> None:
        args = _run_ns()
        card = PlanCard(key="KAN-42", title="t")
        impl = RunOp._build_implement_args(args, card, "acme/widgets")
        assert impl.ticket_key == "KAN-42"


class TestBuildImplementArgsDefaults:
    """Documented defaults flow through unchanged when flags are omitted."""

    def _impl(self) -> argparse.Namespace:
        args = _run_ns()
        return RunOp._build_implement_args(args, PlanCard(key="A", title="a"), "acme/widgets")

    def test_tracker_default_github_issues(self) -> None:
        assert self._impl().tracker == "github-issues"

    def test_provider_default_github(self) -> None:
        assert self._impl().provider == "github"

    def test_meeting_default_fireflies(self) -> None:
        assert self._impl().meeting == "fireflies"

    def test_meeting_top_k_default_3(self) -> None:
        assert self._impl().meeting_top_k == 3

    def test_meeting_max_bytes_default_50000(self) -> None:
        assert self._impl().meeting_max_bytes == 50_000

    def test_max_iter_default_0(self) -> None:
        assert self._impl().max_iter == 0


# ════════════════════════════════════════════════════════════════════════
# LAYER 2b — store/journal/model/owner/repo reach the right seam end-to-end
# ════════════════════════════════════════════════════════════════════════


class TestRunSeamWiring:
    def _capture_impl(self, mocker) -> dict:
        captured: dict = {}

        def _capture(ns):
            captured["ns"] = ns
            return 0

        mocker.patch("briar.commands.agent.run_implement", side_effect=_capture)
        return captured

    def test_per_card_flags_reach_run_implement_end_to_end(self, cli, store_root, journal_root, fake_llm, mocker) -> None:
        # One pending card; selector picks it then completes. Capture the
        # implement Namespace and assert several flags propagated through the
        # full CLI -> loop -> adapter path (not just the unit adapter).
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        from briar.plan import SelectorActionKind, SelectorDecision

        sel = mocker.MagicMock()
        sel.pick.side_effect = [
            SelectorDecision(kind=SelectorActionKind.PICK, key="A", why="go", branch_parent=""),
            SelectorDecision(kind=SelectorActionKind.COMPLETE, key="", why="done", branch_parent=""),
        ]
        mocker.patch("briar.commands.plan.Selector", return_value=sel)
        mocker.patch("briar.commands.plan.KnowledgeWriter", return_value=mocker.MagicMock())
        captured = self._capture_impl(mocker)

        result = cli(
            "plan",
            "run",
            "demo",
            "--owner",
            "globex",
            "--repo",
            "gadgets",
            "--company",
            "acme",
            "--tracker",
            "jira",
            "--provider",
            "bitbucket",
            "--model",
            "m-9",
            "--max-iter",
            "5",
            "--git-user-name",
            "Bot",
            "--git-user-email",
            "b@x.io",
            "--keep-worktree",
            "--runbook",
            "rb.yml",
            "--knowledge",
            "/kb",
            "--meeting",
            "otter",
            "--meeting-key",
            "MK",
            "--meeting-query",
            "q",
            "--meeting-top-k",
            "4",
            "--meeting-max-bytes",
            "999",
            "--llm",
            "anthropic",
            "--store",
            "file",
            "--root",
            str(store_root),
            "--journal-root",
            str(journal_root),
        )
        assert result.code == ExitCode.OK
        ns = captured["ns"]
        assert ns.owner == "globex"
        assert ns.repo == "gadgets"
        assert ns.ticket_project == "globex/gadgets"
        assert ns.tracker == "jira"
        assert ns.provider == "bitbucket"
        assert ns.model == "m-9"
        assert ns.max_iter == 5
        assert ns.git_user_name == "Bot"
        assert ns.git_user_email == "b@x.io"
        assert ns.keep_worktree is True
        assert ns.runbook == "rb.yml"
        assert ns.knowledge == "/kb"
        assert ns.meeting == "otter"
        assert ns.meeting_key == "MK"
        assert ns.meeting_query == "q"
        assert ns.meeting_top_k == 4
        assert ns.meeting_max_bytes == 999
        assert ns.ticket_key == "A"

    def test_tracker_project_explicit_flag_reaches_implement(self, cli, store_root, journal_root, fake_llm, mocker) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        from briar.plan import SelectorActionKind, SelectorDecision

        sel = mocker.MagicMock()
        sel.pick.side_effect = [
            SelectorDecision(kind=SelectorActionKind.PICK, key="A", why="go", branch_parent=""),
            SelectorDecision(kind=SelectorActionKind.COMPLETE, key="", why="done", branch_parent=""),
        ]
        mocker.patch("briar.commands.plan.Selector", return_value=sel)
        mocker.patch("briar.commands.plan.KnowledgeWriter", return_value=mocker.MagicMock())
        captured = self._capture_impl(mocker)
        cli(
            "plan",
            "run",
            "demo",
            "--owner",
            "acme",
            "--repo",
            "widgets",
            "--company",
            "acme",
            "--tracker-project",
            "BOARD-7",
            "--llm",
            "anthropic",
            "--store",
            "file",
            "--root",
            str(store_root),
            "--journal-root",
            str(journal_root),
        )
        assert captured["ns"].ticket_project == "BOARD-7"
