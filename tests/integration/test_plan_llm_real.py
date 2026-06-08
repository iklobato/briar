"""End-to-end: `briar plan next --llm anthropic` driven through the REAL CLI
and the REAL Anthropic SDK against a wire-level fake of the Messages API.

Only `/v1/messages` is faked. The full path runs for real: argparse dispatch →
`make_llm("anthropic")` → `Selector.pick` → `AnthropicLLM.complete` (SDK httpx +
pydantic parse) → `extract_json` of the model's text → `SelectorDecision` →
rendered stdout. A real plan is persisted to a file `KnowledgeStore` first and
loaded back, so the store round-trip executes too.

The selector prompts the model to return STRICT JSON; we seed that JSON as the
assistant's text content. Messages-API success-body shape modelled on:
  https://docs.anthropic.com/en/api/messages
"""

from __future__ import annotations

import json

import pytest

from briar.plan import save_plan
from briar.plan._models import ImplementationPlan, PlanCard
from briar.storage.file import StoreFile

pytestmark = pytest.mark.integration


def _seed_plan(knowledge_root, *, name: str = "acme-board") -> ImplementationPlan:
    """Persist a two-pending-card plan via the REAL store so `plan next`
    can load it back."""
    plan = ImplementationPlan(
        name=name,
        board_url="jira:ACME",
        tracker="jira",
        project="ACME",
        company="acme",
        cards=[
            PlanCard(key="ACME-1", title="Add login", summary="Build the login form", in_scope=["form", "validation"]),
            PlanCard(key="ACME-2", title="Add logout", summary="Tear down the session", depends_on=["ACME-1"]),
        ],
    )
    save_plan(StoreFile(root=knowledge_root), plan)
    return plan


def _text_response(text: str, *, in_tok: int = 200, out_tok: int = 30) -> dict:
    """A Messages-API success body whose single text block carries `text`."""
    return {
        "id": "msg_selector",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    }


def test_plan_next_pick_decision_from_real_llm(anthropic_at, cli, tmp_path) -> None:
    """The model returns a `pick` for a valid pending card; the printed
    decision must reflect the picked key + why + branch_parent, and the
    real SDK must have POSTed the selector prompt + system to /v1/messages."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _seed_plan(knowledge)

    # The selector parses this JSON out of the model's text via extract_json.
    decision_json = json.dumps({"action": "pick", "key": "ACME-1", "why": "no deps, unblocks ACME-2", "branch_parent": "main"})
    anthropic_at.add("POST", "/v1/messages", _text_response(decision_json))

    res = cli(
        "plan",
        "next",
        "acme-board",
        "--llm",
        "anthropic",
        "--store",
        "file",
        "--root",
        str(knowledge),
        "--journal-root",
        str(tmp_path / "journal"),
        "--format",
        "json",
    )

    assert res.code == 0, res.err
    out = json.loads(res.out)
    # The persisted/printed decision reflects the model's pick.
    assert out["action"] == "pick"
    assert out["key"] == "ACME-1"
    assert out["why"] == "no deps, unblocks ACME-2"
    assert out["branch_parent"] == "main"
    # PICK decisions enrich with the card's title/branch from the loaded plan.
    assert out["title"] == "Add login"

    # The real SDK issued the documented request.
    posts = [r for r in anthropic_at.received if r["path"] == "/v1/messages" and r["method"] == "POST"]
    assert len(posts) == 1
    sent = json.loads(posts[0]["body"])
    assert sent["model"] == "claude-sonnet-4-5"
    assert sent["max_tokens"] == 800  # Selector default
    assert "planner for an engineering agent" in sent["system"]
    # The selector listed both pending cards in the user prompt.
    user_prompt = sent["messages"][0]["content"]
    assert "ACME-1" in user_prompt and "ACME-2" in user_prompt
    assert "Build the login form" in user_prompt


def test_plan_next_complete_decision_from_real_llm(anthropic_at, cli, tmp_path) -> None:
    """The model returns `complete`; the printed decision reflects it and
    carries no card key."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _seed_plan(knowledge)

    anthropic_at.add(
        "POST",
        "/v1/messages",
        _text_response(json.dumps({"action": "complete", "why": "all shipped"})),
    )

    res = cli(
        "plan",
        "next",
        "acme-board",
        "--llm",
        "anthropic",
        "--root",
        str(knowledge),
        "--journal-root",
        str(tmp_path / "journal"),
        "--format",
        "json",
    )

    assert res.code == 0, res.err
    out = json.loads(res.out)
    assert out["action"] == "complete"
    assert out["why"] == "all shipped"
    assert out["key"] == ""


def test_plan_next_rejects_pick_of_unknown_card(anthropic_at, cli, tmp_path) -> None:
    """Validation on the way back: the model picks a key that is NOT a
    pending card. The selector must raise → CLI exits non-zero with a clear
    message (the LLM's freedom is bounded, per _selector.py)."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _seed_plan(knowledge)

    anthropic_at.add(
        "POST",
        "/v1/messages",
        _text_response(json.dumps({"action": "pick", "key": "ACME-999", "why": "hallucinated"})),
    )

    res = cli(
        "plan",
        "next",
        "acme-board",
        "--llm",
        "anthropic",
        "--root",
        str(knowledge),
        "--journal-root",
        str(tmp_path / "journal"),
        "--format",
        "json",
    )

    assert res.code != 0
    # The error names the bad key and the valid pending set.
    assert "ACME-999" in res.err
    assert "ACME-1" in res.err


def test_plan_next_rejects_unparseable_llm_text(anthropic_at, cli, tmp_path) -> None:
    """The model returns prose with no JSON object. extract_json yields
    None → the selector raises → CLI exits non-zero. Covers the
    malformed-response path."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _seed_plan(knowledge)

    anthropic_at.add("POST", "/v1/messages", _text_response("I am not sure, let me think about it."))

    res = cli(
        "plan",
        "next",
        "acme-board",
        "--llm",
        "anthropic",
        "--root",
        str(knowledge),
        "--journal-root",
        str(tmp_path / "journal"),
        "--format",
        "json",
    )

    assert res.code != 0
    assert "unparseable" in res.err.lower()
