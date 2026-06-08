"""Parametric flag-effect tests for every `briar plan` subcommand flag.

Companion to tests/unit/commands/test_plan_cmd.py (dispatch + happy path),
tests/test_plan.py (domain primitives) and tests/test_plan_run.py (run loop).
This file's contract: EVERY flag in /tmp/cli_manifest/plan.md has at least one
test that asserts its *observable effect* — the value reaches the persisted
plan, the rendered output, or the mocked collaborator seam (build_plan / the
store factory / the journal factory / the LLM selector / replan / the
`agent implement` runner). A swapped, dropped, negated, or defaulted-wrong flag
must make a test here FAIL.

Seams mocked (no network, no real LLM/board/SDK):
  * briar.commands.plan.build_plan        — board fetch + synthesis
  * briar.commands.plan.make_store        — KnowledgeStore factory (--store/--root)
  * briar.commands.plan.make_journal_store— JournalStore factory (--journal-*)
  * briar.commands.plan.make_llm          — LLM resolution (--llm/--model)
  * briar.commands.plan.Selector / replan — selector + replan primitives
  * briar.commands.agent.run_implement    — the per-card implement call

Where the effect is on the *persisted* plan we use a REAL file store so the
write is genuinely observable; where the effect is "a value reaches a factory"
we spy on the factory and assert the captured argument.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from briar.commands._enums import ExitCode
from briar.plan import ImplementationPlan, PlanCard, SelectorActionKind, SelectorDecision, save_plan
from briar.plan._enums import PlanCardStatus
from briar.storage import make_store

# ─── shared fixtures / helpers ──────────────────────────────────────────


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


def _store_args(root: Path) -> list[str]:
    return ["--store", "file", "--root", str(root)]


class _FakeLLM:
    """Available LLMProvider whose `complete` is never reached (Selector /
    replan / run_implement are all mocked at the command seam)."""

    kind = "fake"

    def is_available(self) -> bool:
        return True

    def complete(self, *, system, messages, tools, max_tokens):  # pragma: no cover
        raise AssertionError("LLM.complete must not be reached — seam is mocked")

    def format_tool_result(self, tool_call_id, output, is_error=False):  # pragma: no cover
        return {}


@pytest.fixture
def fake_llm(mocker: Any) -> "_LLMSpy":
    """Patch `make_llm` so any `--llm <kind>` resolves to an available fake,
    and record the (kind, model) it was called with so `--llm`/`--model`
    effects are assertable."""
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


@pytest.fixture
def store_spy(mocker: Any):
    """Wrap (not replace) `make_store` so the real file store still backs
    persistence while we capture the (kind, file_root) every call received."""
    import briar.commands.plan as plan_mod

    real = plan_mod.make_store
    calls: list[tuple[str, Any]] = []

    def _wrapped(kind, *, file_root=None, **kw):
        calls.append((kind, file_root))
        return real(kind, file_root=file_root, **kw)

    mocker.patch.object(plan_mod, "make_store", side_effect=_wrapped)
    return calls


@pytest.fixture
def journal_spy(mocker: Any):
    """Wrap `make_journal_store`; capture (kind, file_root)."""
    import briar.commands.plan as plan_mod

    real = plan_mod.make_journal_store
    calls: list[tuple[str, Any]] = []

    def _wrapped(kind, *, file_root=None, **kw):
        calls.append((kind, file_root))
        return real(kind, file_root=file_root, **kw)

    mocker.patch.object(plan_mod, "make_journal_store", side_effect=_wrapped)
    return calls


# ════════════════════════════════════════════════════════════════════════
# build  (board, --name, --default-branch, --max-cards, --with-knowledge,
#         --print, --dry-run, --llm, --model, --store, --root, --company)
# ════════════════════════════════════════════════════════════════════════


class TestBuildFlagEffects:
    def _patch_build(self, mocker, built: ImplementationPlan):
        return mocker.patch("briar.commands.plan.build_plan", return_value=built)

    def test_board_positional_reaches_build_plan(self, cli, store_root, mocker) -> None:
        bp = self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        cli("plan", "build", "jira:ENG", "--name", "demo", *_store_args(store_root))
        assert bp.call_args.kwargs["board_url"] == "jira:ENG"

    def test_board_positional_required(self, cli, store_root) -> None:
        result = cli("plan", "build", *_store_args(store_root))
        assert result.code == 2
        assert "board" in result.err.lower()

    def test_name_flag_overrides_slug(self, cli, store_root, mocker) -> None:
        bp = self._patch_build(mocker, _plan(PlanCard(key="A", title="a"), name="chosen"))
        cli("plan", "build", "jira:ENG", "--name", "chosen", *_store_args(store_root))
        assert bp.call_args.kwargs["name"] == "chosen"

    def test_name_default_is_derived_slug(self, cli, store_root, mocker) -> None:
        # Omitting --name => the slug helper derives a non-empty name from the URL.
        bp = self._patch_build(mocker, _plan(PlanCard(key="A", title="a"), name="kan"))
        cli("plan", "build", "https://x.atlassian.net/jira/software/projects/KAN/boards/34", *_store_args(store_root))
        derived = bp.call_args.kwargs["name"]
        assert derived and derived != ""  # non-empty derived slug, not the empty default

    @pytest.mark.parametrize("branch", ["main", "develop", "release/v2"])
    def test_default_branch_reaches_build_plan(self, cli, store_root, mocker, branch) -> None:
        bp = self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        cli("plan", "build", "jira:ENG", "--name", "demo", "--default-branch", branch, *_store_args(store_root))
        assert bp.call_args.kwargs["default_branch"] == branch

    def test_default_branch_default_is_main(self, cli, store_root, mocker) -> None:
        bp = self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        cli("plan", "build", "jira:ENG", "--name", "demo", *_store_args(store_root))
        assert bp.call_args.kwargs["default_branch"] == "main"

    @pytest.mark.parametrize("n", [1, 7, 200])
    def test_max_cards_reaches_build_plan_as_int(self, cli, store_root, mocker, n) -> None:
        bp = self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        cli("plan", "build", "jira:ENG", "--name", "demo", "--max-cards", str(n), *_store_args(store_root))
        assert bp.call_args.kwargs["max_cards"] == n
        assert isinstance(bp.call_args.kwargs["max_cards"], int)

    def test_max_cards_default_is_50(self, cli, store_root, mocker) -> None:
        bp = self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        cli("plan", "build", "jira:ENG", "--name", "demo", *_store_args(store_root))
        assert bp.call_args.kwargs["max_cards"] == 50

    def test_max_cards_rejects_non_int(self, cli, store_root) -> None:
        result = cli("plan", "build", "jira:ENG", "--name", "demo", "--max-cards", "lots", *_store_args(store_root))
        assert result.code == 2

    def test_with_knowledge_splices_company_blob_into_context(self, cli, store_root, mocker) -> None:
        # Seed a company knowledge blob; --with-knowledge + --company must
        # gather it and pass it as a context section to build_plan.
        make_store("file", file_root=store_root).put("knowledge:acme", "COMPANY-FACTS", category="knowledge")
        bp = self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        cli("plan", "build", "jira:ENG", "--name", "demo", "--company", "acme", "--with-knowledge", *_store_args(store_root))
        sections = bp.call_args.kwargs["context_sections"]
        assert any("COMPANY-FACTS" in s for s in sections)

    def test_without_with_knowledge_context_is_empty(self, cli, store_root, mocker) -> None:
        make_store("file", file_root=store_root).put("knowledge:acme", "COMPANY-FACTS", category="knowledge")
        bp = self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        cli("plan", "build", "jira:ENG", "--name", "demo", "--company", "acme", *_store_args(store_root))
        # Flag absent => the gather is skipped => no company facts spliced in.
        assert bp.call_args.kwargs["context_sections"] == []

    def test_print_appends_markdown_after_persist(self, cli, store_root, mocker) -> None:
        self._patch_build(mocker, _plan(PlanCard(key="KAN-77", title="seventyseven")))
        result = cli("plan", "build", "jira:ENG", "--name", "demo", "--print", *_store_args(store_root))
        assert result.code == ExitCode.OK
        # Persisted AND printed.
        assert "KAN-77" in result.out
        from briar.plan import list_plans

        assert "plan:demo" in list_plans(make_store("file", file_root=store_root))

    def test_no_print_omits_markdown(self, cli, store_root, mocker) -> None:
        self._patch_build(mocker, _plan(PlanCard(key="KAN-77", title="seventyseven")))
        result = cli("plan", "build", "jira:ENG", "--name", "demo", *_store_args(store_root))
        # Without --print the card key markdown body is NOT echoed.
        assert "KAN-77" not in result.out

    def test_dry_run_prints_and_does_not_persist(self, cli, store_root, mocker) -> None:
        self._patch_build(mocker, _plan(PlanCard(key="KAN-5", title="five")))
        result = cli("plan", "build", "jira:ENG", "--name", "demo", "--dry-run", *_store_args(store_root))
        assert result.code == ExitCode.OK
        assert "KAN-5" in result.out  # --dry-run implies print
        from briar.plan import list_plans

        assert list_plans(make_store("file", file_root=store_root)) == []  # nothing persisted

    def test_without_dry_run_persists(self, cli, store_root, mocker) -> None:
        self._patch_build(mocker, _plan(PlanCard(key="KAN-5", title="five")))
        cli("plan", "build", "jira:ENG", "--name", "demo", *_store_args(store_root))
        from briar.plan import list_plans

        assert "plan:demo" in list_plans(make_store("file", file_root=store_root))

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini", "bedrock"])
    def test_llm_choice_resolves_provider(self, cli, store_root, mocker, fake_llm, provider) -> None:
        self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        cli("plan", "build", "jira:ENG", "--name", "demo", "--llm", provider, *_store_args(store_root))
        assert fake_llm.kind == provider  # the chosen provider reached make_llm

    def test_llm_empty_default_skips_make_llm(self, cli, store_root, mocker) -> None:
        ml = mocker.patch("briar.commands.plan.make_llm")
        self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        cli("plan", "build", "jira:ENG", "--name", "demo", *_store_args(store_root))
        # Empty --llm => heuristics-only => make_llm is never called, llm=None.
        ml.assert_not_called()
        from briar.commands.plan import build_plan as _  # noqa: F401

    def test_llm_passes_none_to_build_when_empty(self, cli, store_root, mocker) -> None:
        bp = self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        cli("plan", "build", "jira:ENG", "--name", "demo", *_store_args(store_root))
        assert bp.call_args.kwargs["llm"] is None

    def test_llm_invalid_choice_exit_2(self, cli, store_root) -> None:
        result = cli("plan", "build", "jira:ENG", "--name", "demo", "--llm", "wat", *_store_args(store_root))
        assert result.code == 2
        assert "--llm" in result.err

    def test_model_reaches_make_llm(self, cli, store_root, mocker, fake_llm) -> None:
        self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        cli("plan", "build", "jira:ENG", "--name", "demo", "--llm", "anthropic", "--model", "claude-x", *_store_args(store_root))
        assert fake_llm.model == "claude-x"

    @pytest.mark.parametrize("store_kind", ["file", "postgres"])
    def test_store_choice_reaches_factory(self, cli, store_root, mocker, store_spy, store_kind) -> None:
        self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        # postgres store would fail to open; build_plan is mocked but _open_store
        # runs first. For postgres we only assert the factory received the kind,
        # so make the factory a pure spy for that lane.
        if store_kind == "postgres":
            import briar.commands.plan as plan_mod

            captured: dict = {}

            def _fake(kind, *, file_root=None, **kw):
                captured["kind"] = kind
                raise SystemExit(0)  # short-circuit before touching a DB

            mocker.patch.object(plan_mod, "make_store", side_effect=_fake)
            cli("plan", "build", "jira:ENG", "--name", "demo", "--store", "postgres", "--root", str(store_root))
            assert captured["kind"] == "postgres"
        else:
            cli("plan", "build", "jira:ENG", "--name", "demo", *_store_args(store_root))
            assert store_spy[0][0] == "file"

    def test_store_invalid_choice_exit_2(self, cli, store_root) -> None:
        result = cli("plan", "build", "jira:ENG", "--name", "demo", "--store", "mysql", "--root", str(store_root))
        assert result.code == 2
        assert "--store" in result.err

    def test_root_reaches_store_factory(self, cli, store_root, mocker, store_spy) -> None:
        self._patch_build(mocker, _plan(PlanCard(key="A", title="a")))
        cli("plan", "build", "jira:ENG", "--name", "demo", *_store_args(store_root))
        assert store_spy[0][1] == Path(str(store_root))

    def test_company_seeds_namespaced_knowledge_blob(self, cli, store_root, mocker) -> None:
        self._patch_build(mocker, _plan(PlanCard(key="A", title="a"), company="globex"))
        cli("plan", "build", "jira:ENG", "--name", "demo", "--company", "globex", *_store_args(store_root))
        # build_plan is mocked but it returns a plan whose .company drives the seed key.
        assert make_store("file", file_root=store_root).get("knowledge:globex.demo")

    def test_company_default_empty_writes_no_seed(self, cli, store_root, mocker) -> None:
        self._patch_build(mocker, _plan(PlanCard(key="A", title="a"), company=""))
        cli("plan", "build", "jira:ENG", "--name", "demo", *_store_args(store_root))
        # Empty company => no knowledge:<company>.<plan> seed.
        assert make_store("file", file_root=store_root).get("knowledge:.demo") == ""


# ════════════════════════════════════════════════════════════════════════
# Uniform store/company trio across read subcommands: show / status / list
# ════════════════════════════════════════════════════════════════════════


class TestReadSubcommandStoreFlags:
    @pytest.mark.parametrize(
        "argv_builder",
        [
            pytest.param(lambda root: ["plan", "show", "demo", *_store_args(root)], id="show"),
            pytest.param(lambda root: ["plan", "status", "demo", *_store_args(root)], id="status"),
            pytest.param(lambda root: ["plan", "list", *_store_args(root)], id="list"),
        ],
    )
    def test_store_root_reaches_factory(self, cli, store_root, store_spy, argv_builder) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        cli(*argv_builder(store_root))
        assert store_spy[0] == ("file", Path(str(store_root)))

    @pytest.mark.parametrize("sub", ["show", "status", "list"])
    def test_store_invalid_choice_exit_2(self, cli, store_root, sub) -> None:
        argv = ["plan", sub] + ([] if sub == "list" else ["demo"]) + ["--store", "redis", "--root", str(store_root)]
        result = cli(*argv)
        assert result.code == 2
        assert "--store" in result.err

    @pytest.mark.parametrize("sub", ["show", "status"])
    def test_name_positional_required(self, cli, store_root, sub) -> None:
        result = cli("plan", sub, *_store_args(store_root))
        assert result.code == 2

    def test_root_is_used_to_locate_plan(self, cli, tmp_path) -> None:
        # A plan seeded under root_a must NOT be visible when --root points at root_b.
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        _seed(root_a, _plan(PlanCard(key="A", title="a")))
        hit = cli("plan", "show", "demo", "--store", "file", "--root", str(root_a))
        miss = cli("plan", "show", "demo", "--store", "file", "--root", str(root_b))
        assert hit.code == ExitCode.OK
        assert miss.code == ExitCode.GENERAL_ERROR

    def test_company_flag_accepted_and_inert_for_file_store(self, cli, store_root) -> None:
        # --company is wired on every subcommand; for the file store it does not
        # change the resolved blob (file store ignores DSN/company). The command
        # still succeeds and renders the plan.
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        result = cli("plan", "show", "demo", "--company", "anyco", *_store_args(store_root))
        assert result.code == ExitCode.OK
        assert "A" in result.out


# ════════════════════════════════════════════════════════════════════════
# Global --format effect on output-emitting read subcommands
# ════════════════════════════════════════════════════════════════════════


class TestFormatFlag:
    def test_status_json_format_emits_json(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a", status=PlanCardStatus.DONE)))
        result = cli("plan", "status", "demo", "--format", "json", *_store_args(store_root))
        assert result.code == ExitCode.OK
        import json

        payload = json.loads(result.out)
        assert payload["counts"]["done"] == 1
        # JSON path is NOT the table renderer => no "DONE (1)" section header.
        assert "DONE (1)" not in result.out

    def test_status_default_is_table(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a", status=PlanCardStatus.DONE)))
        result = cli("plan", "status", "demo", *_store_args(store_root))
        assert "DONE (1)" in result.out  # table section header present by default

    def test_list_json_format_is_machine_readable(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a"), name="alpha"))
        result = cli("plan", "list", "--format", "json", *_store_args(store_root))
        import json

        names = [row["name"] for row in json.loads(result.out)]
        # list renders the raw blob keys (`plan:<name>`), one row per plan.
        assert names == ["plan:alpha"]


# ════════════════════════════════════════════════════════════════════════
# status / next journal flags (--journal-store, --journal-root)
# ════════════════════════════════════════════════════════════════════════


class TestJournalFlags:
    @pytest.mark.parametrize(
        "argv_builder",
        [
            pytest.param(lambda root, jroot: ["plan", "status", "demo", *_store_args(root), "--journal-root", str(jroot)], id="status"),
        ],
    )
    def test_journal_root_reaches_factory(self, cli, store_root, journal_root, journal_spy, argv_builder) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        cli(*argv_builder(store_root, journal_root))
        assert journal_spy[0] == ("file", Path(str(journal_root)))

    def test_journal_store_default_is_file(self, cli, store_root, journal_spy) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        cli("plan", "status", "demo", *_store_args(store_root))
        assert journal_spy[0][0] == "file"
        assert journal_spy[0][1] == Path("./journal")  # documented default root

    def test_journal_store_invalid_choice_exit_2(self, cli, store_root) -> None:
        result = cli("plan", "status", "demo", "--journal-store", "postgres", *_store_args(store_root))
        assert result.code == 2
        assert "--journal-store" in result.err

    def test_next_journal_root_reaches_factory(self, cli, store_root, journal_root, journal_spy, fake_llm, mocker) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        sel = mocker.MagicMock()
        sel.pick.return_value = SelectorDecision(kind=SelectorActionKind.COMPLETE, key="", why="done", branch_parent="")
        mocker.patch("briar.commands.plan.Selector", return_value=sel)
        cli("plan", "next", "demo", "--llm", "anthropic", "--journal-root", str(journal_root), *_store_args(store_root))
        assert journal_spy[0] == ("file", Path(str(journal_root)))


# ════════════════════════════════════════════════════════════════════════
# next  (name, --llm REQUIRED, --model, store trio, journal pair)
# ════════════════════════════════════════════════════════════════════════


class TestNextFlags:
    def _patch_selector(self, mocker, decision: SelectorDecision):
        sel = mocker.MagicMock()
        sel.pick.return_value = decision
        mocker.patch("briar.commands.plan.Selector", return_value=sel)
        return sel

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini", "bedrock"])
    def test_llm_choice_resolves_provider(self, cli, store_root, fake_llm, mocker, provider) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        self._patch_selector(mocker, SelectorDecision(kind=SelectorActionKind.PICK, key="A", why="go", branch_parent=""))
        result = cli("plan", "next", "demo", "--llm", provider, *_store_args(store_root))
        assert result.code == ExitCode.OK
        assert fake_llm.kind == provider

    def test_llm_required_omission_exit_2(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        result = cli("plan", "next", "demo", *_store_args(store_root))
        assert result.code == 2
        assert "--llm" in result.err

    def test_llm_empty_string_not_a_valid_choice(self, cli, store_root) -> None:
        # For `next`, choices exclude '' (required) — explicit empty must be rejected.
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        result = cli("plan", "next", "demo", "--llm", "", *_store_args(store_root))
        assert result.code == 2

    def test_model_reaches_make_llm(self, cli, store_root, fake_llm, mocker) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        self._patch_selector(mocker, SelectorDecision(kind=SelectorActionKind.PICK, key="A", why="go", branch_parent=""))
        cli("plan", "next", "demo", "--llm", "anthropic", "--model", "m-2", *_store_args(store_root))
        assert fake_llm.model == "m-2"

    def test_decision_pick_renders_key_and_action(self, cli, store_root, fake_llm, mocker) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a"), PlanCard(key="B", title="b")))
        self._patch_selector(mocker, SelectorDecision(kind=SelectorActionKind.PICK, key="B", why="B first", branch_parent=""))
        result = cli("plan", "next", "demo", "--llm", "anthropic", *_store_args(store_root))
        assert "B" in result.out and "pick" in result.out

    def test_store_root_reaches_factory(self, cli, store_root, store_spy, fake_llm, mocker) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        self._patch_selector(mocker, SelectorDecision(kind=SelectorActionKind.COMPLETE, key="", why="d", branch_parent=""))
        cli("plan", "next", "demo", "--llm", "anthropic", *_store_args(store_root))
        assert store_spy[0] == ("file", Path(str(store_root)))


# ════════════════════════════════════════════════════════════════════════
# advance  (name, --card REQUIRED, --status choices, store trio)
# ════════════════════════════════════════════════════════════════════════


class TestAdvanceFlags:
    def _reload(self, root: Path) -> ImplementationPlan:
        from briar.plan import load_plan

        return load_plan(make_store("file", file_root=root), "demo")

    @pytest.mark.parametrize("status", ["pending", "in_progress", "done", "blocked"])
    def test_status_choice_persists_on_target_card(self, cli, store_root, status) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a"), PlanCard(key="B", title="b")))
        result = cli("plan", "advance", "demo", "--card", "A", "--status", status, *_store_args(store_root))
        assert result.code == ExitCode.OK
        reloaded = self._reload(store_root)
        statuses = {c.key: c.status.value for c in reloaded.cards}
        # Only A mutated; B untouched (proves --card targets the right card).
        assert statuses == {"A": status, "B": "pending"}

    def test_status_default_is_done(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        cli("plan", "advance", "demo", "--card", "A", *_store_args(store_root))
        assert self._reload(store_root).cards[0].status is PlanCardStatus.DONE

    def test_status_invalid_choice_exit_2(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        result = cli("plan", "advance", "demo", "--card", "A", "--status", "frozen", *_store_args(store_root))
        assert result.code == 2
        assert "--status" in result.err

    def test_card_required_omission_exit_2(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        result = cli("plan", "advance", "demo", *_store_args(store_root))
        assert result.code == 2
        assert "--card" in result.err

    def test_card_unknown_exits_1(self, cli, store_root) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        result = cli("plan", "advance", "demo", "--card", "NOPE", *_store_args(store_root))
        assert result.code == ExitCode.GENERAL_ERROR
        assert "NOPE" in result.err

    def test_name_required(self, cli, store_root) -> None:
        result = cli("plan", "advance", "--card", "A", *_store_args(store_root))
        assert result.code == 2

    def test_store_root_reaches_factory(self, cli, store_root, store_spy) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        cli("plan", "advance", "demo", "--card", "A", *_store_args(store_root))
        assert store_spy[0] == ("file", Path(str(store_root)))


# ════════════════════════════════════════════════════════════════════════
# clear  (name, --yes, store trio)
# ════════════════════════════════════════════════════════════════════════


class TestClearFlags:
    def test_yes_skips_confirm_and_deletes(self, cli, store_root, mocker) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        confirm = mocker.patch("briar.commands.plan.confirm")
        result = cli("plan", "clear", "demo", "--yes", *_store_args(store_root))
        assert result.code == ExitCode.OK
        confirm.assert_not_called()  # --yes bypasses the prompt entirely
        from briar.plan import list_plans

        assert list_plans(make_store("file", file_root=store_root)) == []

    def test_without_yes_prompts_and_declines(self, cli, store_root, mocker) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        confirm = mocker.patch("briar.commands.plan.confirm", return_value=False)
        result = cli("plan", "clear", "demo", *_store_args(store_root))
        confirm.assert_called_once()
        assert result.code == ExitCode.GENERAL_ERROR
        assert "aborted" in result.out
        from briar.plan import list_plans

        assert "plan:demo" in list_plans(make_store("file", file_root=store_root))

    def test_without_yes_confirm_true_deletes(self, cli, store_root, mocker) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        mocker.patch("briar.commands.plan.confirm", return_value=True)
        result = cli("plan", "clear", "demo", *_store_args(store_root))
        assert result.code == ExitCode.OK
        from briar.plan import list_plans

        assert list_plans(make_store("file", file_root=store_root)) == []

    def test_name_required(self, cli, store_root) -> None:
        result = cli("plan", "clear", "--yes", *_store_args(store_root))
        assert result.code == 2

    def test_store_root_reaches_factory(self, cli, store_root, store_spy) -> None:
        _seed(store_root, _plan(PlanCard(key="A", title="a")))
        cli("plan", "clear", "demo", "--yes", *_store_args(store_root))
        assert store_spy[0] == ("file", Path(str(store_root)))
