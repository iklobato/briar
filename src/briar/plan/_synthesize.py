"""Per-card synthesis: in-scope, out-of-scope, risks, inferred deps.

Two strategies, picked at call time:

  * `LLMSynthesiser` — sends the card body + every available context
    section (knowledge blobs for the company, related cards' titles)
    to an `LLMProvider` and parses a JSON response.
  * `HeuristicSynthesiser` — no LLM. Pulls in-scope/out-of-scope from
    heading-style sections of the card body, parses "Depends on
    KAN-N" / "Blocked by #42" lines, and falls back to a one-line
    summary of the first paragraph.

The CLI tries the LLM path first when an `LLMProvider` is available,
falls back to the heuristic path otherwise. Either way the result
is the same `PlanCard` shape — downstream graph code never has to
care which path produced it."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import List, Optional

from briar.agent._llm import LLMProvider
from briar.plan._json_utils import extract_json
from briar.plan._models import PlanCard

log = logging.getLogger(__name__)


_HEADING_RE = re.compile(r"^\s*(?:#+|\*\*)\s*(?P<head>[^*#:\n][^\n:]*)\s*[:*#]*\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*•]\s+(?P<item>.+?)\s*$", re.MULTILINE)
_DEP_KEY_RE = re.compile(
    r"(?:depends on|blocked by|requires|after)\s*[:\-]?\s*([A-Za-z0-9_/#\-]+)",
    re.IGNORECASE,
)

_BRANCH_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,42}[a-z0-9])$")
_BRANCH_TYPES = frozenset({"feat", "fix", "chore", "refactor", "docs", "test", "perf", "style", "ci", "build"})


def _validate_branch_name(name: str) -> str:
    """Accept only conventional-commits `<type>/<kebab-slug>` where
    `<type>` is one of `_BRANCH_TYPES` and `<slug>` is 3-44 chars of
    lowercase kebab — no double-dash, no leading/trailing dash.
    Anything else returns `""` so the caller's fallback
    (`suggest_branch(key)`) takes over."""
    type_, slash, slug = (name or "").strip().partition("/")
    if not slash or type_ not in _BRANCH_TYPES:
        return ""
    if "--" in slug or not _BRANCH_SLUG_RE.match(slug):
        return ""
    return f"{type_}/{slug}"


class CardSynthesiser(ABC):
    @abstractmethod
    def enrich(self, card: PlanCard, *, board_card_keys: List[str], context_sections: List[str]) -> PlanCard: ...


class HeuristicSynthesiser(CardSynthesiser):
    """No LLM. Parse heading-style scope blocks and dep lines out of
    whatever body the board reader gave us."""

    name = "heuristic"

    def enrich(self, card: PlanCard, *, board_card_keys: List[str], context_sections: List[str]) -> PlanCard:
        body = card.summary or ""
        card.summary = _first_paragraph(body) or card.title
        card.in_scope = card.in_scope or _bullets_under(body, ("in scope", "scope", "acceptance criteria", "deliverables"))
        card.out_of_scope = card.out_of_scope or _bullets_under(body, ("out of scope", "non-goals", "not in scope"))
        card.risks = card.risks or _bullets_under(body, ("risks", "risk", "concerns", "open questions"))

        merged_deps = list(card.depends_on)
        for match in _DEP_KEY_RE.finditer(body):
            raw = match.group(1).strip()
            normalized = _normalise_key(raw, board_card_keys)
            if normalized and normalized not in merged_deps and normalized != card.key:
                merged_deps.append(normalized)
        card.depends_on = [d for d in merged_deps if d in board_card_keys or d.startswith("#") or "-" in d]
        return card


class LLMSynthesiser(CardSynthesiser):
    """Ask an `LLMProvider` to fill in scope / out-of-scope / risks /
    deps as one JSON object per card. Strictly best-effort — if the
    model returns malformed JSON or the call fails, we log and return
    the card unchanged so the heuristic pass (chained after) still
    runs."""

    name = "llm"

    SYSTEM = (
        "You are a planning assistant. For one ticket-shaped piece of work, return STRICT JSON "
        "with keys: summary (<=240 chars, one paragraph), in_scope (list of strings), "
        "out_of_scope (list of strings), risks (list of strings), depends_on (list of strings — "
        "only ids/keys present in the supplied board_card_keys; never invent), "
        "branch_name (string in conventional-commits `<type>/<slug>` form. `<type>` MUST be "
        "one of: feat, fix, chore, refactor, docs, test, perf, style, ci, build — chosen "
        "from the card's actual work (bug fix → `fix/...`; behavior-preserving refactor → "
        "`refactor/...`; new feature → `feat/...`; tests-only → `test/...`; dependency / "
        "tooling chore → `chore/...`). `<slug>` is 3-44 chars of lowercase ASCII kebab-case "
        "derived from the title's distinguishing nouns/verbs — NOT the tracker key, NOT "
        "generic stop-words. Example: title `Refactor profile model imports` → "
        "`refactor/profile-imports`. Drop articles, repo names, epic prefixes. If you "
        "cannot derive a meaningful slug, return an empty string and the heuristic "
        "fallback will assign one). "
        "Use evidence from the supplied context sections; do not fabricate. "
        "Return ONLY the JSON object, no prose, no code fences."
    )

    def __init__(self, llm: LLMProvider, *, max_tokens: int = 1200) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    def enrich(self, card: PlanCard, *, board_card_keys: List[str], context_sections: List[str]) -> PlanCard:
        prompt = self._build_prompt(card, board_card_keys=board_card_keys, context_sections=context_sections)
        try:
            response = self._llm.complete(
                system=self.SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=self._max_tokens,
            )
        except Exception:  # noqa: BLE001 — synthesis is best-effort
            log.exception("plan: LLM synthesis failed for %s", card.key)
            return card
        payload = extract_json(response.text)
        if not payload:
            log.warning("plan: LLM returned no parseable JSON for %s", card.key)
            return card
        card.summary = str(payload.get("summary") or card.summary or card.title)[:1500]
        card.in_scope = [str(x) for x in (payload.get("in_scope") or [])][:20]
        card.out_of_scope = [str(x) for x in (payload.get("out_of_scope") or [])][:20]
        card.risks = [str(x) for x in (payload.get("risks") or [])][:20]
        llm_deps = [str(x) for x in (payload.get("depends_on") or [])]
        merged = list(card.depends_on)
        for d in llm_deps:
            if d in board_card_keys and d not in merged and d != card.key:
                merged.append(d)
        card.depends_on = merged
        if not card.branch_name:
            card.branch_name = _validate_branch_name(str(payload.get("branch_name") or ""))
        return card

    @staticmethod
    def _build_prompt(card: PlanCard, *, board_card_keys: List[str], context_sections: List[str]) -> str:
        parts: List[str] = []
        parts.append(f"Card key: {card.key}")
        parts.append(f"Card title: {card.title}")
        parts.append(f"Card URL: {card.url}")
        parts.append("")
        parts.append("Card body:")
        parts.append(card.summary or card.title or "(empty)")
        parts.append("")
        parts.append("Other board card keys (use as candidate dep targets — NEVER invent new ones):")
        parts.append(", ".join(board_card_keys) if board_card_keys else "(none)")
        if context_sections:
            parts.append("")
            parts.append("Additional context:")
            for section in context_sections:
                parts.append(section.strip())
                parts.append("")
        parts.append("Return STRICT JSON only.")
        return "\n".join(parts)


class CompositeSynthesiser(CardSynthesiser):
    """Run multiple synthesisers in order; each one only fills fields
    the previous left empty. Lets the LLM pass take the lead while
    the heuristic pass guarantees deterministic defaults."""

    def __init__(self, primary: CardSynthesiser, *, fallback: CardSynthesiser) -> None:
        self._primary = primary
        self._fallback = fallback

    def enrich(self, card: PlanCard, *, board_card_keys: List[str], context_sections: List[str]) -> PlanCard:
        card = self._primary.enrich(card, board_card_keys=board_card_keys, context_sections=context_sections)
        return self._fallback.enrich(card, board_card_keys=board_card_keys, context_sections=context_sections)


def make_synthesiser(llm: Optional[LLMProvider]) -> CardSynthesiser:
    """Pick the right synthesiser given an optional LLM. The heuristic
    pass always runs second to guarantee deterministic defaults."""
    heuristic = HeuristicSynthesiser()
    if llm is None or not llm.is_available():
        return heuristic
    return CompositeSynthesiser(LLMSynthesiser(llm), fallback=heuristic)


# ─── helpers ────────────────────────────────────────────────────────


def _first_paragraph(body: str) -> str:
    body = (body or "").strip()
    if not body:
        return ""
    chunk = body.split("\n\n", 1)[0]
    return chunk.strip()[:600]


def _bullets_under(body: str, heading_keywords: tuple) -> List[str]:
    """Find a heading whose text matches any of `heading_keywords`,
    then collect the bullet items immediately under it (until the
    next heading or blank-line block)."""
    if not body:
        return []
    lines = body.splitlines()
    heading_indices: List[int] = []
    for i, line in enumerate(lines):
        if _HEADING_RE.match(line):
            heading_indices.append(i)
    if not heading_indices:
        return []
    targets = {kw.lower() for kw in heading_keywords}
    out: List[str] = []
    for idx in heading_indices:
        match = _HEADING_RE.match(lines[idx])
        head = (match.group("head") or "").strip().lower() if match else ""
        if head not in targets:
            continue
        end = len(lines)
        for j in range(idx + 1, len(lines)):
            if _HEADING_RE.match(lines[j]):
                end = j
                break
        block = "\n".join(lines[idx + 1 : end])
        for bullet in _BULLET_RE.finditer(block):
            text = bullet.group("item").strip()
            if text and text not in out:
                out.append(text)
        if out:
            break
    return out[:20]


def _normalise_key(raw: str, board_card_keys: List[str]) -> str:
    """Match a free-text dep reference to a known card key. Returns
    `""` when no plausible match exists."""
    if not raw:
        return ""
    upper = raw.upper()
    if upper in board_card_keys:
        return upper
    # Try `#42` form against `owner/repo#42` keys.
    if upper.startswith("#"):
        suffix = upper
        for key in board_card_keys:
            if key.endswith(suffix):
                return key
        return upper
    return raw if raw in board_card_keys else ""
