"""Uncovered real-logic branches in the plan helpers: store load/render
error paths, `extract_json` fallbacks, and the heuristic synthesiser's
key-normalisation + bullet-parsing edges.

These are the branches the happy-path suite skips: malformed/missing plan
blobs, the raw-JSON fallback wrapper, prose-wrapped JSON, `#42 → owner/
repo#42` dependency matching, and scope blocks with no matching heading.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from briar.errors import CliError
from briar.plan._json_utils import extract_json
from briar.plan._models import ImplementationPlan, PlanCard
from briar.plan._store import blob_name_for, load_plan, render_markdown, render_plan_knowledge, save_plan
from briar.plan._synthesize import HeuristicSynthesiser, _normalise_key
from briar.storage import make_store

# ─── _store: name + load error paths ───────────────────────────────────


class TestBlobName:
    def test_spaces_become_dashes(self):
        assert blob_name_for("my plan") == "plan:my-plan"

    def test_already_prefixed_passes_through(self):
        assert blob_name_for("plan:demo") == "plan:demo"

    def test_empty_name_raises(self):
        with pytest.raises(CliError, match="plan name required"):
            blob_name_for("")


class TestLoadErrors:
    def _store(self, tmp):
        return make_store("file", file_root=Path(tmp))

    def test_missing_plan_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(CliError, match="plan not found"):
                load_plan(self._store(tmp), "ghost")

    def test_blob_without_json_block_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.put("plan:nojson", "# just a heading, no fenced json here", category="plan")
            with pytest.raises(CliError, match="did not contain a JSON block"):
                load_plan(store, "nojson")

    def test_malformed_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.put("plan:bad", "```json\n{not valid json,,}\n```", category="plan")
            with pytest.raises(CliError, match="malformed JSON"):
                load_plan(store, "bad")

    def test_raw_json_fallback_loads(self):
        # A plan stored as bare JSON (no markdown fence) still round-trips
        # via the `_extract_payload` `{`-prefix fallback.
        import json

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            plan = ImplementationPlan(name="raw", board_url="", tracker="jira", project="X")
            store.put("plan:raw", json.dumps(plan.to_dict()), category="plan")
            loaded = load_plan(store, "raw")
            assert loaded.name == "raw"


class TestRenderMarkdown:
    def test_full_card_renders_all_sections(self):
        plan = ImplementationPlan(
            name="demo",
            board_url="jira:KAN",
            tracker="jira",
            project="KAN",
            company="acme",
            created_at="2026-01-01",
            cards=[
                PlanCard(
                    key="KAN-1",
                    title="Login",
                    url="https://x/KAN-1",
                    summary="Body text",
                    in_scope=["OAuth"],
                    out_of_scope=["SSO"],
                    risks=["token expiry"],
                    sources=["jira:KAN-1"],
                    depends_on=["KAN-0"],
                    branch_name="feat/login",
                    branch_parent="main",
                )
            ],
        )
        md = render_markdown(plan)
        assert "# Plan — demo" in md
        assert "- Company: acme" in md
        assert "### 1. KAN-1 — Login" in md
        assert "- Depends on: KAN-0" in md
        assert "**In scope**" in md
        assert "- OAuth" in md
        assert "**Out of scope**" in md
        assert "**Risks / open questions**" in md
        assert "_Sources: jira:KAN-1_" in md
        # Raw payload round-trips losslessly.
        import json

        payload = md.split("```json\n", 1)[1].split("\n```", 1)[0]
        reloaded = ImplementationPlan.from_dict(json.loads(payload))
        assert reloaded.cards[0].in_scope == ["OAuth"]

    def test_save_then_load_preserves_owner_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store("file", file_root=Path(tmp))
            plan = ImplementationPlan(name="demo", board_url="", tracker="jira", project="X", owner="acme", repo="widgets")
            save_plan(store, plan)
            loaded = load_plan(store, "demo")
            assert (loaded.owner, loaded.repo) == ("acme", "widgets")


class TestRenderPlanKnowledge:
    def test_includes_repo_line_when_owner_and_repo(self):
        plan = ImplementationPlan(name="demo", board_url="jira:KAN", tracker="jira", project="KAN", owner="acme", repo="widgets")
        seed = render_plan_knowledge(plan)
        assert "- Repo: acme/widgets" in seed

    def test_omits_repo_line_without_both(self):
        plan = ImplementationPlan(name="demo", board_url="", tracker="jira", project="KAN", owner="acme", repo="")
        assert "- Repo:" not in render_plan_knowledge(plan)


# ─── _json_utils: fallback branches ────────────────────────────────────


class TestExtractJson:
    def test_empty_returns_none(self):
        assert extract_json("") is None
        assert extract_json(None) is None

    def test_plain_json(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}

    def test_prose_wrapped_json_recovered_by_brace_scan(self):
        text = 'Sure! Here is the plan:\n{"action": "pick", "key": "A"} \nHope that helps.'
        assert extract_json(text) == {"action": "pick", "key": "A"}

    def test_no_braces_returns_none(self):
        assert extract_json("just some prose with no object") is None

    def test_non_dict_top_level_returns_none(self):
        # A JSON array is valid JSON but not dict-shaped → None.
        assert extract_json("[1, 2, 3]") is None

    def test_unrecoverable_garbage_returns_none(self):
        assert extract_json("{ broken : , }") is None


# ─── _synthesize: heuristic key-normalisation + bullets ────────────────


class TestNormaliseKey:
    def test_exact_upper_match(self):
        assert _normalise_key("kan-1", ["KAN-1", "KAN-2"]) == "KAN-1"

    def test_hash_matches_owner_repo_key(self):
        assert _normalise_key("#42", ["acme/widgets#42"]) == "acme/widgets#42"

    def test_hash_without_match_returns_hash(self):
        assert _normalise_key("#99", ["acme/widgets#42"]) == "#99"

    def test_unknown_returns_empty(self):
        assert _normalise_key("RANDOM-7", ["KAN-1"]) == ""

    def test_empty_returns_empty(self):
        assert _normalise_key("", ["KAN-1"]) == ""


class TestHeuristicEdges:
    def test_no_matching_heading_leaves_scope_empty(self):
        card = PlanCard(key="X", title="t", summary="Just a paragraph.\n\n## Random Heading\n- bullet")
        out = HeuristicSynthesiser().enrich(card, board_card_keys=["X"], context_sections=[])
        assert out.in_scope == []
        assert out.out_of_scope == []

    def test_summary_falls_back_to_title_when_body_empty(self):
        card = PlanCard(key="X", title="The Title", summary="")
        out = HeuristicSynthesiser().enrich(card, board_card_keys=["X"], context_sections=[])
        assert out.summary == "The Title"

    def test_hash_dep_resolved_to_namespaced_key(self):
        card = PlanCard(key="acme/widgets#5", title="t", summary="Depends on #4")
        out = HeuristicSynthesiser().enrich(card, board_card_keys=["acme/widgets#4", "acme/widgets#5"], context_sections=[])
        assert out.depends_on == ["acme/widgets#4"]
