"""Tests for `briar plan` — URL parsing, dep-graph ordering, cascade
chaining, heuristic synthesis, and persistence round-trip.

External I/O (tracker APIs, GitHub GraphQL, LLMs) is stubbed so the
tests run offline."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from briar.errors import CliError
from briar.plan import (
    BoardReaderRegistry,
    ImplementationPlan,
    PlanCard,
    apply_cascade,
    build_plan,
    delete_plan,
    list_plans,
    load_plan,
    make_synthesiser,
    resolve_board,
    save_plan,
    topological_sort,
)
from briar.plan._boards.github_project import GithubProjectBoardReader
from briar.plan._boards.jira_board import JiraBoardReader
from briar.plan._synthesize import HeuristicSynthesiser
from briar.storage import make_store


class BoardUrlParsingTests(unittest.TestCase):
    def test_jira_full_url(self) -> None:
        reader = JiraBoardReader()
        url = "https://acme.atlassian.net/jira/software/projects/KAN/boards/34"
        self.assertTrue(reader.matches(url))
        ref = reader.parse(url)
        self.assertEqual(ref.tracker, "jira")
        self.assertEqual(ref.project, "KAN")
        self.assertEqual(ref.extra("board_id"), "34")
        self.assertEqual(ref.base_url, "https://acme.atlassian.net")

    def test_jira_short_form(self) -> None:
        reader = JiraBoardReader()
        ref = reader.parse("jira:KAN")
        self.assertEqual(ref.project, "KAN")
        self.assertEqual(ref.tracker, "jira")

    def test_jira_rejects_garbage(self) -> None:
        reader = JiraBoardReader()
        self.assertFalse(reader.matches("https://example.com/not-jira"))
        with self.assertRaises(CliError):
            reader.parse("https://example.com/not-jira")

    def test_github_org_project(self) -> None:
        reader = GithubProjectBoardReader()
        url = "https://github.com/orgs/bitspark-co/projects/34"
        self.assertTrue(reader.matches(url))
        ref = reader.parse(url)
        self.assertEqual(ref.tracker, "github-project")
        self.assertEqual(ref.owner, "bitspark-co")
        self.assertEqual(ref.extra("scope"), "orgs")
        self.assertEqual(ref.extra("number"), "34")

    def test_github_user_project(self) -> None:
        reader = GithubProjectBoardReader()
        ref = reader.parse("https://github.com/users/iklobato/projects/2")
        self.assertEqual(ref.owner, "iklobato")
        self.assertEqual(ref.extra("scope"), "users")
        self.assertEqual(ref.extra("number"), "2")

    def test_registry_resolves_both(self) -> None:
        kinds = BoardReaderRegistry.kinds()
        self.assertIn("jira", kinds)
        self.assertIn("github-project", kinds)
        self.assertIsInstance(
            resolve_board("jira:ENG"),
            JiraBoardReader,
        )
        self.assertIsInstance(
            resolve_board("https://github.com/orgs/foo/projects/1"),
            GithubProjectBoardReader,
        )

    def test_registry_unknown_url_raises(self) -> None:
        with self.assertRaises(CliError):
            resolve_board("https://example.com/nothing")


class TopologicalSortTests(unittest.TestCase):
    def test_orders_after_deps(self) -> None:
        a = PlanCard(key="A", title="alpha")
        b = PlanCard(key="B", title="beta", depends_on=["A"])
        c = PlanCard(key="C", title="gamma", depends_on=["B"])
        ordered = topological_sort([c, b, a])  # input order shuffled
        self.assertEqual([card.key for card in ordered], ["A", "B", "C"])

    def test_stable_order_independents(self) -> None:
        a = PlanCard(key="A", title="alpha")
        b = PlanCard(key="B", title="beta")
        c = PlanCard(key="C", title="gamma")
        ordered = topological_sort([a, b, c])
        self.assertEqual([card.key for card in ordered], ["A", "B", "C"])

    def test_trims_out_of_board_deps(self) -> None:
        a = PlanCard(key="A", title="alpha", depends_on=["ZZ-999"])
        ordered = topological_sort([a])
        self.assertEqual(ordered[0].depends_on, [])

    def test_cycle_raises(self) -> None:
        a = PlanCard(key="A", title="alpha", depends_on=["B"])
        b = PlanCard(key="B", title="beta", depends_on=["A"])
        with self.assertRaises(CliError):
            topological_sort([a, b])


class CascadeTests(unittest.TestCase):
    def test_no_cascade_defaults_branch_parent(self) -> None:
        cards = [
            PlanCard(key="A", title="alpha"),
            PlanCard(key="B", title="beta", depends_on=["A"]),
        ]
        cards = topological_sort(cards)
        out = apply_cascade(cards, cascade=False, default_branch="dev")
        self.assertEqual(out[0].branch_parent, "dev")
        self.assertEqual(out[1].branch_parent, "dev")
        self.assertEqual(out[0].branch_name, "briar/a")
        self.assertEqual(out[1].branch_name, "briar/b")

    def test_cascade_chains_branches_through_deps(self) -> None:
        a = PlanCard(key="A", title="alpha")
        b = PlanCard(key="B", title="beta", depends_on=["A"])
        c = PlanCard(key="C", title="gamma", depends_on=["B"])
        ordered = topological_sort([a, b, c])
        out = apply_cascade(ordered, cascade=True, default_branch="main")
        self.assertEqual(out[0].branch_parent, "main")
        self.assertEqual(out[1].branch_parent, out[0].branch_name)
        self.assertEqual(out[2].branch_parent, out[1].branch_name)

    def test_cascade_picks_latest_dep_when_many(self) -> None:
        a = PlanCard(key="A", title="alpha")
        b = PlanCard(key="B", title="beta")
        c = PlanCard(key="C", title="gamma", depends_on=["A", "B"])
        ordered = topological_sort([a, b, c])
        out = apply_cascade(ordered, cascade=True, default_branch="main")
        # `B` comes after `A` in the topo order; cascade should pick it
        # over `A` as the parent of `C`.
        self.assertEqual(out[-1].branch_parent, out[1].branch_name)


class HeuristicSynthesiserTests(unittest.TestCase):
    def test_extracts_scope_blocks(self) -> None:
        body = (
            "Top-line summary of the work to do.\n\n"
            "## In Scope\n- Build authn middleware\n- Unit tests\n\n"
            "## Out of Scope\n- Refactoring the billing module\n\n"
            "## Risks\n- Token rotation timing\n"
        )
        card = PlanCard(key="X", title="Sample", summary=body)
        out = HeuristicSynthesiser().enrich(card, board_card_keys=["X"], context_sections=[])
        self.assertEqual(out.in_scope, ["Build authn middleware", "Unit tests"])
        self.assertEqual(out.out_of_scope, ["Refactoring the billing module"])
        self.assertEqual(out.risks, ["Token rotation timing"])
        self.assertIn("Top-line", out.summary)

    def test_picks_up_depends_on_lines(self) -> None:
        body = "Implementation note\n\nDepends on: KAN-1\nBlocked by KAN-2"
        card = PlanCard(key="KAN-3", title="C", summary=body)
        out = HeuristicSynthesiser().enrich(
            card, board_card_keys=["KAN-1", "KAN-2", "KAN-3"], context_sections=[]
        )
        self.assertIn("KAN-1", out.depends_on)
        self.assertIn("KAN-2", out.depends_on)
        self.assertNotIn("KAN-3", out.depends_on)


class FakeReader:
    """Stand-in `BoardReader` for `build_plan` end-to-end tests."""

    kind = "fake"

    def __init__(self, cards):
        self._cards = cards

    def matches(self, url: str) -> bool:
        return True

    def parse(self, url: str):
        from briar.plan._board import BoardRef

        return BoardRef(tracker="fake", project="FAKE", url=url, owner="acme")

    def fetch(self, ref, *, company, max_cards):
        return list(self._cards)


class BuildPlanTests(unittest.TestCase):
    def test_end_to_end_with_cascade(self) -> None:
        cards = [
            PlanCard(key="A", title="alpha", summary="do alpha"),
            PlanCard(key="B", title="beta", summary="needs alpha", depends_on=["A"]),
            PlanCard(key="C", title="gamma", summary="needs beta", depends_on=["B"]),
        ]
        plan = build_plan(
            board_url="fake://board",
            name="demo",
            cascade=True,
            default_branch="main",
            reader=FakeReader(cards),
        )
        self.assertEqual([c.key for c in plan.cards], ["A", "B", "C"])
        self.assertTrue(plan.cascade)
        self.assertEqual(plan.cards[0].branch_parent, "main")
        self.assertEqual(plan.cards[1].branch_parent, plan.cards[0].branch_name)
        self.assertEqual(plan.cards[2].branch_parent, plan.cards[1].branch_name)


class StoreRoundtripTests(unittest.TestCase):
    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store("file", file_root=Path(tmp))
            plan = ImplementationPlan(
                name="demo",
                board_url="jira:KAN",
                tracker="jira",
                project="KAN",
                cascade=True,
                cards=[
                    PlanCard(key="KAN-1", title="one", branch_name="briar/kan-1", branch_parent="main"),
                    PlanCard(
                        key="KAN-2",
                        title="two",
                        depends_on=["KAN-1"],
                        branch_name="briar/kan-2",
                        branch_parent="briar/kan-1",
                    ),
                ],
            )
            blob = save_plan(store, plan)
            self.assertTrue(blob.startswith("plan:"))
            self.assertIn("plan:demo", list_plans(store))

            reloaded = load_plan(store, "demo")
            self.assertEqual(reloaded.name, plan.name)
            self.assertEqual([c.key for c in reloaded.cards], ["KAN-1", "KAN-2"])
            self.assertEqual(reloaded.cards[1].branch_parent, "briar/kan-1")
            self.assertTrue(reloaded.cascade)

    def test_next_pending_respects_status(self) -> None:
        plan = ImplementationPlan(
            name="demo",
            board_url="",
            tracker="jira",
            project="KAN",
            cards=[
                PlanCard(key="A", title="a"),
                PlanCard(key="B", title="b", depends_on=["A"]),
            ],
        )
        self.assertEqual(plan.next_pending().key, "A")
        plan.cards[0].status = "done"
        self.assertEqual(plan.next_pending().key, "B")
        plan.cards[1].status = "done"
        self.assertIsNone(plan.next_pending())

    def test_delete_removes_blob(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store("file", file_root=Path(tmp))
            plan = ImplementationPlan(name="gone", board_url="", tracker="jira", project="X")
            save_plan(store, plan)
            self.assertTrue(delete_plan(store, "gone"))
            self.assertFalse(delete_plan(store, "gone"))


class SynthesiserPickerTests(unittest.TestCase):
    def test_returns_heuristic_when_no_llm(self) -> None:
        synth = make_synthesiser(None)
        self.assertIsInstance(synth, HeuristicSynthesiser)

    def test_returns_heuristic_when_llm_unavailable(self) -> None:
        fake_llm = mock.Mock()
        fake_llm.is_available.return_value = False
        synth = make_synthesiser(fake_llm)
        # Composite would be returned when available; here we expect
        # heuristic-only.
        self.assertIsInstance(synth, HeuristicSynthesiser)


if __name__ == "__main__":
    unittest.main()
