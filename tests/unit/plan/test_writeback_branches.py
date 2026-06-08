"""KnowledgeWriter — the failure-mode branches `tests/test_plan.py::
KnowledgeWriterTests` leaves uncovered: unavailable LLM rejected at
construct, the LLM-call-raises path (best-effort → False, no write), an
empty `body` field, and a no-op `put_if_changed` returning the same body.

Writeback is deliberately best-effort: every failure returns False and
must NOT raise, so a broken merge never aborts a card's completion.
"""

from __future__ import annotations

import json

import pytest

from briar.errors import CliError
from briar.plan._models import ImplementationPlan, PlanCard
from briar.plan._writeback import KnowledgeWriter


class _Store:
    """In-memory KnowledgeStore stand-in with put_if_changed semantics."""

    def __init__(self, seed=None):
        self._data = dict(seed or {})
        self.put_calls = []

    def get(self, name):
        return self._data.get(name, "")

    def put_if_changed(self, name, content, category=""):
        prev = self._data.get(name, "")
        wrote = prev != content
        if wrote:
            self._data[name] = content
        self.put_calls.append((name, content, wrote))
        from types import SimpleNamespace

        return SimpleNamespace(wrote=wrote, byte_count=len(content), new_hash="", prev_hash="", ref=None)


class _LLM:
    def __init__(self, *, text="", raises=None):
        self._text = text
        self._raises = raises
        self.last_prompt = None

    def is_available(self):
        return True

    def complete(self, *, system, messages, tools, max_tokens):
        self.last_prompt = messages[0]["content"]
        if self._raises:
            raise self._raises
        from briar.agent._llm import LLMResponse

        return LLMResponse(text=self._text, tool_calls=[], stop_reason="end_turn", input_tokens=0, output_tokens=0)


def _plan(company="acme", name="demo"):
    return ImplementationPlan(name=name, board_url="", tracker="jira", project="X", company=company)


def _card():
    return PlanCard(key="KAN-1", title="Login", summary="body", in_scope=["OAuth"])


class TestConstruct:
    def test_unavailable_llm_rejected(self):
        llm = _LLM()
        llm.is_available = lambda: False
        with pytest.raises(CliError, match="writeback requires an available LLM"):
            KnowledgeWriter(llm)


class TestFailureModes:
    def test_llm_exception_returns_false_no_write(self):
        store = _Store()
        llm = _LLM(raises=TimeoutError("upstream"))
        result = KnowledgeWriter(llm).write(store=store, plan=_plan(), card=_card(), diff="d")
        assert result is False
        assert store.put_calls == []

    def test_empty_body_returns_false_no_write(self):
        store = _Store()
        llm = _LLM(text=json.dumps({"body": "   "}))
        result = KnowledgeWriter(llm).write(store=store, plan=_plan(), card=_card(), diff="d")
        assert result is False
        assert store.put_calls == []

    def test_missing_name_skips(self):
        store = _Store()
        llm = _LLM(text=json.dumps({"body": "x"}))
        result = KnowledgeWriter(llm).write(store=store, plan=_plan(name=""), card=_card(), diff="d")
        assert result is False
        assert store.put_calls == []

    def test_no_op_write_returns_false(self):
        # When the model returns the existing body, put_if_changed reports
        # wrote=False and write() reflects that.
        existing = "# knowledge\n- fact"
        store = _Store(seed={"knowledge:acme.demo": existing})
        llm = _LLM(text=json.dumps({"body": existing}))
        result = KnowledgeWriter(llm).write(store=store, plan=_plan(), card=_card(), diff="d")
        assert result is False
        assert store.put_calls == [("knowledge:acme.demo", existing, False)]


class TestHappyAndPrompt:
    def test_writes_and_returns_true(self):
        store = _Store(seed={"knowledge:acme.demo": "old"})
        llm = _LLM(text=json.dumps({"body": "# new\n- learned X"}))
        result = KnowledgeWriter(llm).write(store=store, plan=_plan(), card=_card(), diff="the diff")
        assert result is True
        assert store.get("knowledge:acme.demo") == "# new\n- learned X"

    def test_prompt_includes_card_and_prior_blob(self):
        store = _Store(seed={"knowledge:acme.demo": "PRIOR-BODY"})
        llm = _LLM(text=json.dumps({"body": "new"}))
        KnowledgeWriter(llm).write(store=store, plan=_plan(), card=_card(), diff="DIFF-TEXT")
        prompt = llm.last_prompt
        assert "key: KAN-1" in prompt
        assert "title: Login" in prompt
        assert "in_scope: OAuth" in prompt
        assert "DIFF-TEXT" in prompt
        assert "PRIOR-BODY" in prompt

    def test_prompt_diff_none_marker_when_empty(self):
        store = _Store()
        llm = _LLM(text=json.dumps({"body": "new"}))
        KnowledgeWriter(llm).write(store=store, plan=_plan(), card=_card(), diff="")
        assert "(none)" in llm.last_prompt
