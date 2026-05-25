"""Unit tests for the dashboard's plan + journal collectors and the
extended knowledge collector's shard detection. Pure dict in / dict
out — no HTTP, no real stores."""

from __future__ import annotations

from pathlib import Path

import pytest

from briar.dashboard.collectors import JournalSessionsCollector, KnowledgeCollector, PlansCollector
from briar.dashboard.server import _human_age, _human_bytes, _short_id
from briar.plan import ImplementationPlan, PlanCard, save_plan
from briar.plan._enums import PlanCardStatus
from briar.storage import make_store

# ─── helpers ────────────────────────────────────────────────────────


class _FakeJournalRef:
    def __init__(self, session_id, command, target, started_at="", ended_at="", decision_count=0):
        self.session_id = session_id
        self.command = command
        self.target = target
        self.started_at = started_at
        self.ended_at = ended_at
        self.decision_count = decision_count


class _FakeDE:
    def __init__(self, choice, value="", rationale=""):
        self.choice = choice
        self.value = value
        self.rationale = rationale
        self.artifacts = {}
        self.timestamp = ""


class _FakeSession:
    def __init__(self, decisions):
        self.decisions = decisions


class _FakeJournalStore:
    """In-memory journal stand-in. `seed` is a list of
    `(ref, decisions)` tuples; the store responds to `list` + `get`."""

    def __init__(self, seed):
        self._refs = []
        self._sessions = {}
        for ref, decisions in seed:
            self._refs.append(ref)
            self._sessions[ref.session_id] = _FakeSession([_FakeDE(**d) for d in decisions])

    def list(self, *, command_prefix="", limit=50):
        out = [r for r in self._refs if (not command_prefix or r.command.startswith(command_prefix))]
        return out[:limit]

    def get(self, session_id):
        return self._sessions.get(session_id)


# ─── PlansCollector ─────────────────────────────────────────────────


class TestPlansCollector:
    def _seed_plan(self, store, name="demo", company="acme"):
        plan = ImplementationPlan(
            name=name,
            board_url="fake://board",
            tracker="fake",
            project="FAKE",
            company=company,
            cards=[
                PlanCard(key="A", title="a", status=PlanCardStatus.DONE),
                PlanCard(key="B", title="b", status=PlanCardStatus.BLOCKED),
                PlanCard(key="C", title="c"),
            ],
        )
        save_plan(store, plan)
        return plan

    def test_counts_statuses_per_plan(self, tmp_path: Path) -> None:
        store = make_store("file", file_root=tmp_path)
        self._seed_plan(store)
        out = PlansCollector(knowledge_store=store).collect()
        assert out["count"] == 1
        row = out["rows"][0]
        assert row["name"] == "demo"
        assert row["total_cards"] == 3
        assert row["counts"]["done"] == 1
        assert row["counts"]["blocked"] == 1
        assert row["counts"]["pending"] == 1
        assert row["knowledge_shard"] == "knowledge:acme.demo"

    def test_no_plans_renders_clean(self, tmp_path: Path) -> None:
        store = make_store("file", file_root=tmp_path)
        out = PlansCollector(knowledge_store=store).collect()
        assert out == {"rows": [], "count": 0}

    def test_pairs_with_journal_last_session(self, tmp_path: Path) -> None:
        store = make_store("file", file_root=tmp_path)
        self._seed_plan(store)
        jstore = _FakeJournalStore(
            [
                (
                    _FakeJournalRef("s1", "plan.run", "demo@acme/widgets", started_at="2026-05-25T16:00:00"),
                    [
                        {"choice": "plan.run.start"},
                        {"choice": "plan.next.decision", "value": "pick", "rationale": "A first"},
                        {"choice": "plan.run.card.completed", "value": "A"},
                        {"choice": "plan.run.card.failed", "value": "B"},
                    ],
                )
            ]
        )
        row = PlansCollector(knowledge_store=store, journal_store=jstore).collect()["rows"][0]
        assert row["last_session_id"] == "s1"
        assert row["last_decision_action"] == "pick"
        assert row["last_decision_why"] == "A first"
        assert row["last_completed_count"] == 1
        assert row["last_failed_count"] == 1


# ─── JournalSessionsCollector ───────────────────────────────────────


class TestJournalSessionsCollector:
    def test_no_journal_store_renders_error_panel(self) -> None:
        out = JournalSessionsCollector(journal_store=None).collect()
        assert out["groups"] == []
        assert "_error" in out

    def test_groups_by_command_prefix(self) -> None:
        jstore = _FakeJournalStore(
            [
                (_FakeJournalRef("s1", "plan.run", "p@o/r", started_at="2026-05-25T10:00:00"), []),
                (_FakeJournalRef("s2", "plan.next", "p", started_at="2026-05-25T11:00:00"), []),
                (_FakeJournalRef("s3", "agent.implement", "o/r#7", started_at="2026-05-25T12:00:00"), []),
                (_FakeJournalRef("s4", "scaffold.implementation", "acme", started_at="2026-05-25T13:00:00"), []),
            ]
        )
        out = JournalSessionsCollector(journal_store=jstore).collect()
        assert out["total"] == 4
        prefixes = {g["prefix"]: g["count"] for g in out["groups"]}
        assert prefixes == {"plan": 2, "agent": 1, "scaffold": 1}

    def test_respects_limit(self) -> None:
        many = [(_FakeJournalRef(f"s{i}", "plan.run", f"p{i}@o/r"), []) for i in range(100)]
        jstore = _FakeJournalStore(many)
        out = JournalSessionsCollector(journal_store=jstore, limit=5).collect()
        assert out["total"] == 5


# ─── KnowledgeCollector — shard detection + full body ───────────────


class TestKnowledgeCollectorExtensions:
    def _store_with(self, tmp_path, items):
        store = make_store("file", file_root=tmp_path)
        for name, body in items.items():
            store.put(name, body, category=name.split(":", 1)[0])
        return store

    def test_shard_paired_with_parent(self, tmp_path: Path) -> None:
        store = self._store_with(
            tmp_path,
            {
                "knowledge:acme": "# company knowledge\n\n## one\nbody",
                "knowledge:acme.q3-plan": "# plan-scoped\n\n## cards\n- a",
            },
        )
        rows = {r["path"]: r for r in KnowledgeCollector(store=store).collect()["rows"]}
        assert rows["knowledge:acme.q3-plan"]["shard_of"] == "knowledge:acme"
        assert rows["knowledge:acme.q3-plan"]["shard_plan"] == "q3-plan"
        assert rows["knowledge:acme"]["shard_of"] == ""

    def test_full_body_returned(self, tmp_path: Path) -> None:
        store = self._store_with(tmp_path, {"knowledge:acme": "# title\n\nbody line"})
        row = KnowledgeCollector(store=store).collect()["rows"][0]
        assert "body line" in row["body"]
        assert row["body_truncated"] is False

    def test_body_capped_when_huge(self, tmp_path: Path) -> None:
        huge = "x" * 100_000
        store = self._store_with(tmp_path, {"knowledge:big": huge})
        row = KnowledgeCollector(store=store).collect()["rows"][0]
        assert len(row["body"]) == KnowledgeCollector._BODY_CAP_BYTES
        assert row["body_truncated"] is True


# ─── Jinja filters ──────────────────────────────────────────────────


class TestFilters:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, "0 B"),
            (512, "512 B"),
            (1024, "1.0 KB"),
            (1_500_000, "1.4 MB"),
            (None, ""),
            ("not a number", ""),
        ],
    )
    def test_human_bytes(self, value, expected) -> None:
        assert _human_bytes(value) == expected

    def test_human_age_empty_inputs(self) -> None:
        assert _human_age("") == ""
        assert _human_age(None) == ""
        assert _human_age("not iso") == ""

    def test_short_id(self) -> None:
        assert _short_id("abcdef1234567890") == "abcdef12"
        assert _short_id(None) == ""
