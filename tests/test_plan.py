"""Tests for `briar plan` — URL parsing, build_plan, persistence, and the
LLM-driven primitives (Selector, KnowledgeWriter, replan, status).

External I/O (tracker APIs, GitHub GraphQL, real LLMs) is stubbed so the
tests run offline."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from briar.agent._llm import LLMResponse
from briar.errors import CliError
from briar.plan import (
    BoardReaderRegistry,
    ImplementationPlan,
    KnowledgeWriter,
    PlanCard,
    PlanContext,
    Selector,
    SelectorActionKind,
    build_plan,
    collect_status,
    delete_plan,
    list_plans,
    load_plan,
    make_synthesiser,
    render_plan_knowledge,
    render_table,
    replan,
    resolve_board,
    save_plan,
    suggest_branch,
)
from briar.plan._boards.github_project import GithubProjectBoardReader
from briar.plan._boards.jira_board import JiraBoardReader
from briar.plan._enums import PlanCardStatus
from briar.plan._synthesize import HeuristicSynthesiser
from briar.storage import make_store

# ─── helpers ────────────────────────────────────────────────────────


class FakeLLM:
    """Canned-response `LLMProvider` for offline tests.

    Pass a list of JSON strings or a callable that builds one given the
    prompt; each `complete` call pops the next response."""

    kind = "fake"

    def __init__(self, responses):
        self._responses = list(responses)

    def is_available(self) -> bool:
        return True

    def complete(self, *, system, messages, tools, max_tokens):
        if not self._responses:
            raise AssertionError("FakeLLM ran out of canned responses")
        next_resp = self._responses.pop(0)
        if callable(next_resp):
            text = next_resp(system=system, messages=messages)
        else:
            text = next_resp
        return LLMResponse(
            text=text,
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=0,
            output_tokens=0,
        )

    def format_tool_result(self, tool_call_id, output, is_error=False):
        return {}


class FakeReader:
    """Stand-in `BoardReader` for build_plan / replan tests."""

    kind = "fake"

    def __init__(self, cards):
        self._cards = cards

    def matches(self, url: str) -> bool:
        return True

    def parse(self, url: str):
        from briar.plan._board import BoardRef

        return BoardRef(tracker="fake", project="FAKE", url=url, owner="acme")

    def fetch(self, ref, *, company, max_cards):
        return [PlanCard(**vars(c)) if isinstance(c, _CardKwargs) else c for c in self._cards]


class _CardKwargs:
    """Lightweight transport for parametric card factories in tests."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# ─── URL parsing ────────────────────────────────────────────────────


class BoardUrlParsingTests(unittest.TestCase):
    def test_jira_full_url(self) -> None:
        reader = JiraBoardReader()
        url = "https://example.atlassian.net/jira/software/projects/KAN/boards/34"
        self.assertTrue(reader.matches(url))
        ref = reader.parse(url)
        self.assertEqual(ref.tracker, "jira")
        self.assertEqual(ref.project, "KAN")
        self.assertEqual(ref.extra("board_id"), "34")

    def test_jira_short_form(self) -> None:
        ref = JiraBoardReader().parse("jira:KAN")
        self.assertEqual(ref.project, "KAN")

    def test_jira_rejects_garbage(self) -> None:
        reader = JiraBoardReader()
        self.assertFalse(reader.matches("https://example.com/not-jira"))
        with self.assertRaises(CliError):
            reader.parse("https://example.com/not-jira")

    def test_github_org_project(self) -> None:
        ref = GithubProjectBoardReader().parse("https://github.com/orgs/foo/projects/34")
        self.assertEqual(ref.tracker, "github-project")
        self.assertEqual(ref.owner, "foo")
        self.assertEqual(ref.extra("scope"), "orgs")
        self.assertEqual(ref.extra("number"), "34")

    def test_registry_resolves_both(self) -> None:
        kinds = BoardReaderRegistry.kinds()
        self.assertIn("jira", kinds)
        self.assertIn("github-project", kinds)
        self.assertIsInstance(resolve_board("jira:ENG"), JiraBoardReader)

    def test_registry_unknown_url_raises(self) -> None:
        with self.assertRaises(CliError):
            resolve_board("https://example.com/nothing")


class SuggestBranchTests(unittest.TestCase):
    def test_jira_key(self) -> None:
        self.assertEqual(suggest_branch("KAN-12"), "briar/kan-12")

    def test_issue_hash(self) -> None:
        self.assertEqual(suggest_branch("#42"), "briar/issue-42")

    def test_path_chars_stripped(self) -> None:
        self.assertEqual(suggest_branch("foo/bar"), "briar/foo-bar")


# ─── Heuristic synthesiser (build-time enrichment, NOT picking) ─────


class HeuristicSynthesiserTests(unittest.TestCase):
    def test_extracts_scope_blocks(self) -> None:
        body = "Top-line summary.\n\n" "## In Scope\n- Build authn\n- Tests\n\n" "## Out of Scope\n- Refactor billing\n\n" "## Risks\n- Token timing\n"
        card = PlanCard(key="X", title="Sample", summary=body)
        out = HeuristicSynthesiser().enrich(card, board_card_keys=["X"], context_sections=[])
        self.assertEqual(out.in_scope, ["Build authn", "Tests"])
        self.assertEqual(out.risks, ["Token timing"])

    def test_picks_up_depends_on_lines(self) -> None:
        body = "Implementation note\n\nDepends on: KAN-1\nBlocked by KAN-2"
        card = PlanCard(key="KAN-3", title="C", summary=body)
        out = HeuristicSynthesiser().enrich(card, board_card_keys=["KAN-1", "KAN-2", "KAN-3"], context_sections=[])
        self.assertIn("KAN-1", out.depends_on)
        self.assertIn("KAN-2", out.depends_on)
        self.assertNotIn("KAN-3", out.depends_on)


# ─── build_plan ─────────────────────────────────────────────────────


class BuildPlanTests(unittest.TestCase):
    def test_keeps_board_order_no_cascade(self) -> None:
        cards = [
            PlanCard(key="A", title="alpha"),
            PlanCard(key="B", title="beta", depends_on=["A"]),
            PlanCard(key="C", title="gamma", depends_on=["B"]),
        ]
        plan = build_plan(
            board_url="fake://board",
            name="demo",
            default_branch="main",
            reader=FakeReader(cards),
        )
        # No topological re-ordering — board order preserved.
        self.assertEqual([c.key for c in plan.cards], ["A", "B", "C"])
        # Every card branches from default_branch by default.
        self.assertTrue(all(c.branch_parent == "main" for c in plan.cards))
        # Branch names auto-derived.
        self.assertEqual(plan.cards[0].branch_name, "briar/a")


# ─── Persistence ────────────────────────────────────────────────────


class StoreRoundtripTests(unittest.TestCase):
    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store("file", file_root=Path(tmp))
            plan = ImplementationPlan(
                name="demo",
                board_url="jira:KAN",
                tracker="jira",
                project="KAN",
                cards=[
                    PlanCard(key="KAN-1", title="one", branch_name="briar/kan-1", branch_parent="main"),
                    PlanCard(
                        key="KAN-2",
                        title="two",
                        depends_on=["KAN-1"],
                        branch_name="briar/kan-2",
                        branch_parent="main",
                    ),
                ],
            )
            blob = save_plan(store, plan)
            self.assertTrue(blob.startswith("plan:"))
            self.assertIn("plan:demo", list_plans(store))

            reloaded = load_plan(store, "demo")
            self.assertEqual(reloaded.name, plan.name)
            self.assertEqual([c.key for c in reloaded.cards], ["KAN-1", "KAN-2"])

    def test_delete_removes_blob(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store("file", file_root=Path(tmp))
            plan = ImplementationPlan(name="gone", board_url="", tracker="jira", project="X")
            save_plan(store, plan)
            self.assertTrue(delete_plan(store, "gone"))
            self.assertFalse(delete_plan(store, "gone"))

    def test_legacy_cascade_field_ignored(self) -> None:
        """Pre-v2 plans had a `cascade` field; from_dict must tolerate it."""
        raw = {
            "version": 1,
            "name": "old",
            "board_url": "",
            "tracker": "jira",
            "project": "X",
            "cascade": True,
            "cards": [{"key": "A", "title": "a"}],
        }
        plan = ImplementationPlan.from_dict(raw)
        self.assertEqual(plan.name, "old")
        self.assertEqual(plan.cards[0].key, "A")


class SynthesiserPickerTests(unittest.TestCase):
    def test_returns_heuristic_when_no_llm(self) -> None:
        synth = make_synthesiser(None)
        self.assertIsInstance(synth, HeuristicSynthesiser)


# ─── Selector ───────────────────────────────────────────────────────


def _plan_with(*cards: PlanCard, name="demo", company="acme") -> ImplementationPlan:
    return ImplementationPlan(
        name=name,
        board_url="fake://board",
        tracker="fake",
        project="FAKE",
        company=company,
        cards=list(cards),
    )


def _ctx(**kw) -> PlanContext:
    return PlanContext(**kw)


class SelectorTests(unittest.TestCase):
    def test_pick_action(self) -> None:
        plan = _plan_with(PlanCard(key="A", title="a"), PlanCard(key="B", title="b"))
        llm = FakeLLM([json.dumps({"action": "pick", "key": "B", "why": "B unlocks more"})])
        decision = Selector(llm).pick(plan, _ctx())
        self.assertEqual(decision.kind, SelectorActionKind.PICK)
        self.assertEqual(decision.key, "B")
        self.assertEqual(decision.why, "B unlocks more")

    def test_replan_action(self) -> None:
        plan = _plan_with(PlanCard(key="A", title="a"))
        llm = FakeLLM([json.dumps({"action": "replan", "why": "scope drift"})])
        decision = Selector(llm).pick(plan, _ctx())
        self.assertEqual(decision.kind, SelectorActionKind.REPLAN)
        self.assertEqual(decision.why, "scope drift")

    def test_complete_when_no_pending(self) -> None:
        plan = _plan_with(PlanCard(key="A", title="a", status=PlanCardStatus.DONE))
        # FakeLLM has no responses — selector must short-circuit.
        decision = Selector(FakeLLM([])).pick(plan, _ctx())
        self.assertEqual(decision.kind, SelectorActionKind.COMPLETE)

    def test_invalid_key_raises(self) -> None:
        plan = _plan_with(PlanCard(key="A", title="a"))
        llm = FakeLLM([json.dumps({"action": "pick", "key": "ZZZ"})])
        with self.assertRaises(CliError):
            Selector(llm).pick(plan, _ctx())

    def test_unknown_action_raises(self) -> None:
        plan = _plan_with(PlanCard(key="A", title="a"))
        llm = FakeLLM([json.dumps({"action": "yolo"})])
        with self.assertRaises(CliError):
            Selector(llm).pick(plan, _ctx())

    def test_unparseable_response_raises(self) -> None:
        plan = _plan_with(PlanCard(key="A", title="a"))
        llm = FakeLLM(["not json at all"])
        with self.assertRaises(CliError):
            Selector(llm).pick(plan, _ctx())

    def test_handles_fenced_json(self) -> None:
        plan = _plan_with(PlanCard(key="A", title="a"))
        llm = FakeLLM(['```json\n{"action": "pick", "key": "A"}\n```'])
        self.assertEqual(Selector(llm).pick(plan, _ctx()).kind, SelectorActionKind.PICK)

    def test_unavailable_llm_rejected_at_construct(self) -> None:
        llm = FakeLLM([])
        llm.is_available = lambda: False
        with self.assertRaises(CliError):
            Selector(llm)


# ─── KnowledgeWriter ────────────────────────────────────────────────


class _FakeStore:
    """In-memory KnowledgeStore stand-in for writeback tests."""

    def __init__(self):
        self._data = {}
        self.put_calls = []

    def get(self, name):
        return self._data.get(name, "")

    def put(self, name, content, category=""):
        self._data[name] = content

        class _Ref:
            pass

        r = _Ref()
        r.name = name
        r.byte_count = len(content)
        return r

    def put_if_changed(self, name, content, category=""):
        prev = self._data.get(name, "")
        wrote = prev != content
        if wrote:
            self._data[name] = content

        class _Result:
            pass

        r = _Result()
        r.wrote = wrote
        r.byte_count = len(content)
        r.new_hash = ""
        r.prev_hash = ""
        r.ref = None
        self.put_calls.append((name, content, wrote))
        return r


class KnowledgeWriterTests(unittest.TestCase):
    def test_writes_merged_body(self) -> None:
        store = _FakeStore()
        plan = _plan_with(PlanCard(key="A", title="a"))
        new_body = "# updated knowledge\n- new fact"
        llm = FakeLLM([json.dumps({"body": new_body})])
        wrote = KnowledgeWriter(llm).write(store=store, plan=plan, card=plan.cards[0], diff="diff body")
        self.assertTrue(wrote)
        self.assertEqual(store.get("knowledge:acme.demo"), new_body)

    def test_no_op_when_company_missing(self) -> None:
        store = _FakeStore()
        plan = _plan_with(PlanCard(key="A", title="a"), company="")
        llm = FakeLLM([])
        self.assertFalse(KnowledgeWriter(llm).write(store=store, plan=plan, card=plan.cards[0], diff=""))

    def test_unparseable_response_no_write(self) -> None:
        store = _FakeStore()
        plan = _plan_with(PlanCard(key="A", title="a"))
        llm = FakeLLM(["nonsense"])
        self.assertFalse(KnowledgeWriter(llm).write(store=store, plan=plan, card=plan.cards[0], diff=""))
        self.assertEqual(store.put_calls, [])


# ─── replan ────────────────────────────────────────────────────────


class ReplanTests(unittest.TestCase):
    def test_preserves_status_of_overlapping_keys(self) -> None:
        old = _plan_with(
            PlanCard(key="A", title="a", status=PlanCardStatus.DONE),
            PlanCard(key="B", title="b", status=PlanCardStatus.BLOCKED, last_attempt_summary="boom"),
            PlanCard(key="C", title="c"),
        )
        # Fresh board adds D, drops C, keeps A/B.
        fresh_cards = [
            PlanCard(key="A", title="a"),
            PlanCard(key="B", title="b"),
            PlanCard(key="D", title="d"),
        ]
        reader = FakeReader(fresh_cards)
        new_plan = replan(old, reader=reader, llm=None)
        statuses = {c.key: c.status for c in new_plan.cards}
        self.assertEqual(statuses["A"], PlanCardStatus.DONE)
        self.assertEqual(statuses["B"], PlanCardStatus.BLOCKED)
        self.assertEqual(statuses["D"], PlanCardStatus.PENDING)
        self.assertNotIn("C", statuses)
        b = next(c for c in new_plan.cards if c.key == "B")
        self.assertEqual(b.last_attempt_summary, "boom")


# ─── PlanContext.from_stores ───────────────────────────────────────


class PlanContextTests(unittest.TestCase):
    def _fake_journal_with(self, decisions):
        class _Ref:
            def __init__(self, sid, target):
                self.session_id = sid
                self.target = target

        class _Session:
            def __init__(self, decisions):
                self.decisions = decisions

        class _DE:
            def __init__(self, choice, value="", rationale="", artifacts=None):
                self.choice = choice
                self.value = value
                self.rationale = rationale
                self.artifacts = artifacts or {}
                self.timestamp = ""

        class _Store:
            def list(self, *, command_prefix="", limit=50):
                return [_Ref("s1", "demo@acme/widgets")]

            def get(self, sid):
                return _Session([_DE(**d) for d in decisions])

        return _Store(), _DE

    def test_folds_completed_failed_in_progress(self) -> None:
        store, _ = self._fake_journal_with(
            [
                {"choice": "plan.run.card.start", "value": "A"},
                {"choice": "plan.run.card.completed", "value": "A", "rationale": "ok"},
                {"choice": "plan.run.card.start", "value": "B"},
                {"choice": "plan.run.card.failed", "value": "B", "rationale": "fail"},
                {"choice": "plan.run.card.start", "value": "C"},
            ]
        )
        plan = _plan_with(PlanCard(key="A", title="a"), PlanCard(key="B", title="b"), PlanCard(key="C", title="c"))
        kstore = _FakeStore()
        kstore.put("knowledge:acme.demo", "plan body")
        kstore.put("knowledge:acme", "company body")
        ctx = PlanContext.from_stores(journal_store=store, knowledge_store=kstore, plan=plan)
        self.assertEqual(ctx.completed, [("A", "ok")])
        self.assertEqual(ctx.failed, [("B", "fail")])
        self.assertEqual(ctx.in_progress, "C")
        self.assertEqual(ctx.knowledge, "plan body")
        self.assertEqual(ctx.company_knowledge, "company body")


# ─── status renderer ────────────────────────────────────────────────


class StatusTests(unittest.TestCase):
    def test_buckets_by_status(self) -> None:
        plan = _plan_with(
            PlanCard(key="A", title="a", status=PlanCardStatus.DONE),
            PlanCard(key="B", title="b", status=PlanCardStatus.IN_PROGRESS),
            PlanCard(key="C", title="c", status=PlanCardStatus.BLOCKED, last_attempt_summary="boom"),
            PlanCard(key="D", title="d", status=PlanCardStatus.PENDING),
        )
        snapshot = collect_status(plan, journal_store=None)
        self.assertEqual(snapshot["counts"], {"done": 1, "in_progress": 1, "blocked": 1, "pending": 1})
        self.assertEqual(snapshot["done"][0]["key"], "A")
        self.assertEqual(snapshot["in_progress"][0]["key"], "B")
        self.assertEqual(snapshot["blocked"][0]["last_attempt"], "boom")
        self.assertEqual(snapshot["pending"][0]["key"], "D")

    def test_render_table_includes_each_section(self) -> None:
        plan = _plan_with(
            PlanCard(key="A", title="a", status=PlanCardStatus.DONE),
            PlanCard(key="B", title="b"),
        )
        snapshot = collect_status(plan, journal_store=None)
        text = render_table(snapshot)
        self.assertIn("DONE (1)", text)
        self.assertIn("PENDING (1)", text)
        self.assertIn("A", text)
        self.assertIn("B", text)


# ─── render_plan_knowledge seed ─────────────────────────────────────


class RenderPlanKnowledgeTests(unittest.TestCase):
    def test_seed_includes_cards(self) -> None:
        plan = _plan_with(
            PlanCard(key="A", title="alpha", summary="first card"),
            PlanCard(key="B", title="beta", in_scope=["build x"], risks=["latency"]),
        )
        seed = render_plan_knowledge(plan)
        self.assertIn("# demo — plan knowledge", seed)
        self.assertIn("### A — alpha", seed)
        self.assertIn("### B — beta", seed)
        self.assertIn("first card", seed)
        self.assertIn("build x", seed)
        self.assertIn("latency", seed)


if __name__ == "__main__":
    unittest.main()
