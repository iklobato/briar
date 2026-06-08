"""Command-layer tests for `briar plan` (src/briar/commands/plan.py).

Scope: the wiring from parsed argv -> store/journal/llm/board seams ->
observable effects (plan file written, status table rendered, decision
printed, plan deleted), dispatch correctness, argument validation, and the
documented failure exit codes. The plan-domain primitives (build_plan,
Selector, KnowledgeWriter, replan, collect_status) are covered in
tests/test_plan.py / tests/unit/plan/*; the run-loop is covered in
tests/test_plan_run.py. Here we test the *command* surface end-to-end via
the `cli` fixture, using a REAL file store (so persistence is observable)
and mocking only the LLM / board-fetch / build seams.

No real LLM, board API, or network. Every external boundary is mocked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from briar.commands._enums import ExitCode
from briar.plan import ImplementationPlan, PlanCard, save_plan
from briar.plan._enums import PlanCardStatus
from briar.storage import make_store

# ─── helpers ────────────────────────────────────────────────────────────


def _plan(*cards: PlanCard, name: str = "demo", company: str = "acme") -> ImplementationPlan:
    return ImplementationPlan(
        name=name,
        board_url="jira:KAN",
        tracker="jira",
        project="KAN",
        company=company,
        cards=list(cards),
    )


@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    """A knowledge-store root the CLI will use via `--store file --root`."""
    root = tmp_path / "knowledge"
    root.mkdir()
    return root


def _seed(root: Path, plan: ImplementationPlan) -> None:
    save_plan(make_store("file", file_root=root), plan)


def _store_args(root: Path) -> list[str]:
    return ["--store", "file", "--root", str(root)]


class _FakeLLM:
    """Minimal available LLMProvider so `_require_llm` passes without an SDK.

    `is_available()` True is the only contract `_require_llm` checks; the
    Selector/replan calls are mocked at the command seam, so `complete`
    is never reached in these tests."""

    kind = "fake"

    def is_available(self) -> bool:
        return True

    def complete(self, *, system, messages, tools, max_tokens):  # pragma: no cover
        raise AssertionError("LLM.complete must not be reached — selector is mocked")

    def format_tool_result(self, tool_call_id, output, is_error=False):  # pragma: no cover
        return {}


@pytest.fixture
def fake_llm(mocker: Any) -> _FakeLLM:
    """Patch `make_llm` so any `--llm anthropic` resolves to an available
    fake — no real SDK import, no credentials needed."""
    llm = _FakeLLM()
    mocker.patch("briar.commands.plan.make_llm", return_value=llm)
    return llm


# ─── build ──────────────────────────────────────────────────────────────


class TestBuild:
    def test_build_persists_plan_and_seeds_knowledge(self, cli, store_root, mocker) -> None:
        built = _plan(PlanCard(key="KAN-1", title="one"), PlanCard(key="KAN-2", title="two"))
        bp = mocker.patch("briar.commands.plan.build_plan", return_value=built)
        result = cli("plan", "build", "jira:KAN", "--name", "demo", *_store_args(store_root))
        assert result.code == ExitCode.OK
        # build_plan invoked with the parsed board URL + name.
        kwargs = bp.call_args.kwargs
        assert kwargs["board_url"] == "jira:KAN"
        assert kwargs["name"] == "demo"
        # The plan was persisted: it loads back with both cards.
        from briar.plan import load_plan

        reloaded = load_plan(make_store("file", file_root=store_root), "demo")
        assert [c.key for c in reloaded.cards] == ["KAN-1", "KAN-2"]
        # The plan-knowledge seed blob was written (company is non-empty).
        assert make_store("file", file_root=store_root).get("knowledge:acme.demo")

    def test_build_dry_run_prints_markdown_without_persisting(self, cli, store_root, mocker) -> None:
        built = _plan(PlanCard(key="KAN-1", title="one"))
        mocker.patch("briar.commands.plan.build_plan", return_value=built)
        result = cli("plan", "build", "jira:KAN", "--name", "demo", "--dry-run", *_store_args(store_root))
        assert result.code == ExitCode.OK
        # Markdown went to stdout.
        assert "KAN-1" in result.out
        # Nothing persisted — list_plans is empty.
        from briar.plan import list_plans

        assert list_plans(make_store("file", file_root=store_root)) == []

    def test_build_name_defaults_to_slug_from_board(self, cli, store_root, mocker) -> None:
        built = _plan(PlanCard(key="A", title="a"), name="kan")
        bp = mocker.patch("briar.commands.plan.build_plan", return_value=built)
        # No --name; the slug helper derives one from the URL.
        cli("plan", "build", "https://x.atlassian.net/jira/software/projects/KAN/boards/34", *_store_args(store_root))
        assert bp.call_args.kwargs["name"]  # non-empty derived slug


# ─── show ───────────────────────────────────────────────────────────────


class TestShow:
    def test_show_renders_stored_plan_markdown(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="KAN-9", title="nine", summary="do nine")))
        result = cli("plan", "show", "demo", *_store_args(store_root))
        assert result.code == ExitCode.OK
        assert "KAN-9" in result.out

    def test_show_missing_plan_exits_1(self, cli, store_root) -> None:
        result = cli("plan", "show", "ghost", *_store_args(store_root))
        assert result.code == ExitCode.GENERAL_ERROR
        assert "not found" in result.err


# ─── status ─────────────────────────────────────────────────────────────


class TestStatus:
    def test_status_table_buckets_cards(self, cli, store_root) -> None:
        _seed(
            store_root,
            _plan(
                PlanCard(key="A", title="a", status=PlanCardStatus.DONE),
                PlanCard(key="B", title="b", status=PlanCardStatus.PENDING),
            ),
        )
        result = cli("plan", "status", "demo", *_store_args(store_root))
        assert result.code == ExitCode.OK
        assert "DONE (1)" in result.out
        assert "PENDING (1)" in result.out
        assert "A" in result.out and "B" in result.out

    def test_status_missing_plan_exits_1(self, cli, store_root) -> None:
        result = cli("plan", "status", "ghost", *_store_args(store_root))
        assert result.code == ExitCode.GENERAL_ERROR


# ─── next ───────────────────────────────────────────────────────────────


class TestNext:
    def test_next_prints_selector_decision(self, cli, store_root, fake_llm, mocker) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="alpha"), PlanCard(key="B", title="beta")))
        # Mock the Selector so no LLM round-trip happens; return a PICK.
        from briar.plan import SelectorActionKind, SelectorDecision

        decision = SelectorDecision(kind=SelectorActionKind.PICK, key="B", why="B unblocks", branch_parent="")
        sel = mocker.MagicMock()
        sel.pick.return_value = decision
        mocker.patch("briar.commands.plan.Selector", return_value=sel)
        result = cli("plan", "next", "demo", "--llm", "anthropic", *_store_args(store_root))
        assert result.code == ExitCode.OK
        # The decision dict must carry the picked key + action (render to stdout).
        assert "B" in result.out
        assert "pick" in result.out

    def test_next_requires_llm_flag_exit_2(self, cli, store_root) -> None:
        # `--llm` is required=True for `next`; argparse rejects its absence.
        result = cli("plan", "next", "demo", *_store_args(store_root))
        assert result.code == 2
        assert "--llm" in result.err

    def test_next_unavailable_llm_exits_1(self, cli, store_root, mocker) -> None:
        # make_llm returns an LLM whose is_available() is False → CliError.
        unavail = _FakeLLM()
        unavail.is_available = lambda: False  # type: ignore[method-assign]
        mocker.patch("briar.commands.plan.make_llm", return_value=unavail)
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        result = cli("plan", "next", "demo", "--llm", "anthropic", *_store_args(store_root))
        assert result.code == ExitCode.GENERAL_ERROR
        assert "not available" in result.err


# ─── advance ────────────────────────────────────────────────────────────


class TestAdvance:
    def test_advance_marks_card_and_persists(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a"), PlanCard(key="B", title="b")))
        result = cli("plan", "advance", "demo", "--card", "A", "--status", "done", *_store_args(store_root))
        assert result.code == ExitCode.OK
        from briar.plan import load_plan

        reloaded = load_plan(make_store("file", file_root=store_root), "demo")
        statuses = {c.key: c.status.value for c in reloaded.cards}
        assert statuses == {"A": "done", "B": "pending"}

    def test_advance_default_status_is_done(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        cli("plan", "advance", "demo", "--card", "A", *_store_args(store_root))
        from briar.plan import load_plan

        reloaded = load_plan(make_store("file", file_root=store_root), "demo")
        assert reloaded.cards[0].status is PlanCardStatus.DONE

    def test_advance_unknown_card_exits_1(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        result = cli("plan", "advance", "demo", "--card", "ZZZ", *_store_args(store_root))
        assert result.code == ExitCode.GENERAL_ERROR
        assert "ZZZ" in result.err

    def test_advance_requires_card_flag_exit_2(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        result = cli("plan", "advance", "demo", *_store_args(store_root))
        assert result.code == 2
        assert "--card" in result.err

    def test_advance_invalid_status_choice_exit_2(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        result = cli("plan", "advance", "demo", "--card", "A", "--status", "frozen", *_store_args(store_root))
        assert result.code == 2
        assert "--status" in result.err


# ─── list ───────────────────────────────────────────────────────────────


class TestList:
    def test_list_enumerates_plans_sorted(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a"), name="zeta"))
        _seed(store_root, _plan(PlanCard(key="A", title="a"), name="alpha"))
        result = cli("plan", "list", *_store_args(store_root))
        assert result.code == ExitCode.OK
        # Both names present and alpha appears before zeta (sorted).
        assert "alpha" in result.out and "zeta" in result.out
        assert result.out.index("alpha") < result.out.index("zeta")

    def test_list_empty_store_ok(self, cli, store_root) -> None:
        result = cli("plan", "list", *_store_args(store_root))
        assert result.code == ExitCode.OK


# ─── clear ──────────────────────────────────────────────────────────────


class TestClear:
    def test_clear_yes_deletes_plan(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        result = cli("plan", "clear", "demo", "--yes", *_store_args(store_root))
        assert result.code == ExitCode.OK
        assert "deleted demo" in result.out
        from briar.plan import list_plans

        assert list_plans(make_store("file", file_root=store_root)) == []

    def test_clear_missing_plan_exits_1(self, cli, store_root) -> None:
        result = cli("plan", "clear", "ghost", "--yes", *_store_args(store_root))
        assert result.code == ExitCode.GENERAL_ERROR
        assert "not found ghost" in result.out

    def test_clear_declined_aborts_without_delete(self, cli, store_root, mocker) -> None:
        # No --yes; confirm() returns False (operator answered "no").
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        mocker.patch("briar.commands.plan.confirm", return_value=False)
        result = cli("plan", "clear", "demo", *_store_args(store_root))
        assert result.code == ExitCode.GENERAL_ERROR
        assert "aborted" in result.out
        # Plan still present.
        from briar.plan import list_plans

        assert "plan:demo" in list_plans(make_store("file", file_root=store_root))


# ─── dispatch correctness + top-level validation ────────────────────────


class TestDispatchAndValidation:
    def test_unknown_plan_op_exits_2(self, cli) -> None:
        result = cli("plan", "frobnicate")
        assert result.code == 2

    def test_no_op_exits_2(self, cli) -> None:
        result = cli("plan")
        assert result.code == 2

    def test_dispatch_routes_show_not_status(self, cli, store_root, mocker) -> None:
        # A flipped dispatch (show -> status) would render the status table
        # instead of the markdown. show prints markdown (no "DONE (n)" header).
        _seed(store_root, _plan(PlanCard(key="A", title="a", status=PlanCardStatus.DONE)))
        result = cli("plan", "show", "demo", *_store_args(store_root))
        assert result.code == ExitCode.OK
        assert "DONE (" not in result.out  # status-table marker absent
        assert "A" in result.out

    def test_run_requires_owner_repo_exit_2(self, cli, store_root) -> None:
        # `plan run` has required --owner/--repo; absence is a usage error.
        result = cli("plan", "run", "demo", "--llm", "anthropic", *_store_args(store_root))
        assert result.code == 2
