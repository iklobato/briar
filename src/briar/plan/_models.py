"""Data shapes for `briar plan`.

A plan is an ordered list of `PlanCard`s in the order the board returned
them. Each card carries the extracted ticket summary, in/out-of-scope
notes, the upstream cards it depends on (data, *not* control flow — the
LLM selector reads it as a hint, the runner never uses it to gate
picking), and the git branch metadata the engineer flow will use when
picking the card up. Status moves pending → in_progress → done as the
operator (or the implementer agent) advances through the plan.

The picker used to be `next_pending()` — first pending card whose deps
were all `done`. That algorithm is gone; the LLM selector in
`_selector.py` picks now, using `PlanContext` as input and returning a
`SelectorDecision`. `depends_on` survives as one of the hints the LLM
sees, not as a gate.

The shape is stable across storage backends — the JSON emitted by
`to_dict` is what `plan:<name>` blobs hold."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from briar.plan._enums import PlanCardStatus, SelectorActionKind

PLAN_SCHEMA_VERSION = 2


def suggest_branch(key: str) -> str:
    """`KAN-12` → `chore/kan-12`; `#42` → `chore/issue-42`. Used by
    `build_plan` to seed each card's branch name when the LLM
    synthesiser is absent or returns an invalid name. `chore/` is the
    conservative conventional-commits default; the LLM synthesiser
    overrides with the right type (feat / fix / refactor / test / …)
    when a real classification is possible."""
    slug = key.strip().lower().replace("#", "issue-").replace(" ", "-").replace("/", "-")
    slug = "".join(c for c in slug if c.isalnum() or c in "-_")
    return f"chore/{slug or 'card'}"


@dataclass
class PlanCard:
    """One ticket-shaped unit of work, enriched with synthesis output
    and a resolved branch parent."""

    key: str
    title: str
    url: str = ""
    tracker: str = ""
    summary: str = ""
    in_scope: List[str] = field(default_factory=list)
    out_of_scope: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    branch_name: str = ""
    branch_parent: str = ""
    status: PlanCardStatus = PlanCardStatus.PENDING
    notes: str = ""
    last_attempt_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "PlanCard":
        return cls(
            key=str(raw.get("key") or ""),
            title=str(raw.get("title") or ""),
            url=str(raw.get("url") or ""),
            tracker=str(raw.get("tracker") or ""),
            summary=str(raw.get("summary") or ""),
            in_scope=list(raw.get("in_scope") or []),
            out_of_scope=list(raw.get("out_of_scope") or []),
            risks=list(raw.get("risks") or []),
            sources=list(raw.get("sources") or []),
            depends_on=list(raw.get("depends_on") or []),
            branch_name=str(raw.get("branch_name") or ""),
            branch_parent=str(raw.get("branch_parent") or ""),
            status=PlanCardStatus(raw.get("status") or PlanCardStatus.PENDING.value),
            notes=str(raw.get("notes") or ""),
            last_attempt_summary=str(raw.get("last_attempt_summary") or ""),
        )


@dataclass
class ImplementationPlan:
    """Top-level plan record. `cards` is in the order the board returned
    them; the LLM selector picks any pending card it wants, so this list
    is presentation order, not execution order."""

    name: str
    board_url: str
    tracker: str
    project: str
    company: str = ""
    owner: str = ""
    repo: str = ""
    default_branch: str = "main"
    created_at: str = ""
    cards: List[PlanCard] = field(default_factory=list)
    version: int = PLAN_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "board_url": self.board_url,
            "tracker": self.tracker,
            "project": self.project,
            "company": self.company,
            "owner": self.owner,
            "repo": self.repo,
            "default_branch": self.default_branch,
            "created_at": self.created_at,
            "cards": [c.to_dict() for c in self.cards],
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ImplementationPlan":
        return cls(
            name=str(raw.get("name") or ""),
            board_url=str(raw.get("board_url") or ""),
            tracker=str(raw.get("tracker") or ""),
            project=str(raw.get("project") or ""),
            company=str(raw.get("company") or ""),
            owner=str(raw.get("owner") or ""),
            repo=str(raw.get("repo") or ""),
            default_branch=str(raw.get("default_branch") or "main"),
            created_at=str(raw.get("created_at") or ""),
            cards=[PlanCard.from_dict(c) for c in (raw.get("cards") or [])],
            version=int(raw.get("version") or PLAN_SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class SelectorDecision:
    """One iteration's worth of LLM judgement.

    `kind` discriminates; `key` is set only for `PICK`; `branch_parent`
    is an optional override the LLM may emit when stacking PRs against
    a non-default base makes sense. The runner does one exhaustive match
    on `kind` and never inspects the LLM's free-form reasoning beyond
    journaling `why`."""

    kind: SelectorActionKind
    key: str = ""
    why: str = ""
    branch_parent: str = ""


@dataclass
class PlanContext:
    """Read-only projection of past/current/pending state the LLM sees.

    Built fresh on every selector call from the plan blob, the journal
    store, and `knowledge:<company>.<plan>`. No persistence of its own —
    the storage layer holds the truth; this struct just shapes it for
    the prompt."""

    completed: List[Tuple[str, str]] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)
    in_progress: Optional[str] = None
    knowledge: str = ""
    company_knowledge: str = ""

    @classmethod
    def from_stores(
        cls,
        *,
        journal_store: Any,
        knowledge_store: Any,
        plan: "ImplementationPlan",
        max_knowledge_bytes: int = 8_000,
    ) -> "PlanContext":
        """Assemble a context from the persistent stores. `journal_store`
        is queried for `plan.run` sessions matching the plan name;
        `knowledge_store` is read for `knowledge:<company>.<plan>` plus
        the company-wide `knowledge:<company>` blob.

        Failure modes are deliberately silent: a missing knowledge blob
        or an unreadable journal returns an empty context field. The
        selector is supposed to cope with thin context."""
        completed: List[Tuple[str, str]] = []
        failed: List[Tuple[str, str]] = []
        in_progress: Optional[str] = None
        target_prefix = f"{plan.name}@"
        try:
            refs = journal_store.list(command_prefix="plan.run")
        except Exception:  # noqa: BLE001 — context build is best-effort
            refs = []
        for ref in refs:
            target = getattr(ref, "target", "") or ""
            if not target.startswith(target_prefix):
                continue
            try:
                session = journal_store.get(ref.session_id)
            except Exception:  # noqa: BLE001
                session = None
            if session is None:
                continue
            opened_card: Optional[str] = None
            closed_keys: set = set()
            for ev in session.decisions:
                if ev.choice == "plan.run.card.start":
                    opened_card = str(ev.value or "")
                elif ev.choice == "plan.run.card.completed":
                    key = str(ev.value or "")
                    completed.append((key, ev.rationale or ""))
                    closed_keys.add(key)
                elif ev.choice == "plan.run.card.failed":
                    key = str(ev.value or "")
                    failed.append((key, ev.rationale or ""))
                    closed_keys.add(key)
            if opened_card and opened_card not in closed_keys:
                in_progress = opened_card

        knowledge = ""
        company_knowledge = ""
        try:
            wanted: List[str] = []
            plan_key = f"knowledge:{plan.company}.{plan.name}" if (plan.company and plan.name) else ""
            company_key = f"knowledge:{plan.company}" if plan.company else ""
            if plan_key:
                wanted.append(plan_key)
            if company_key:
                wanted.append(company_key)
            blobs = knowledge_store.get_many(wanted) if wanted else {}
            if plan_key:
                knowledge = blobs.get(plan_key, "")[:max_knowledge_bytes]
            if company_key:
                company_knowledge = blobs.get(company_key, "")[:max_knowledge_bytes]
        except Exception:  # noqa: BLE001
            pass

        return cls(
            completed=completed,
            failed=failed,
            in_progress=in_progress,
            knowledge=knowledge,
            company_knowledge=company_knowledge,
        )
