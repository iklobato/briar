"""`briar plan status` — visualise past / current / to-be-done.

Pure projection over the plan blob and the journal store. No new
persistence — every fact this renders already lives in one of the
two stores. Two output shapes via the existing `briar.formatting.render`
helper: a structured dict (consumed by `--format json/yaml/quiet`) and
a human table (`--format table`)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from briar.plan._enums import PlanCardStatus
from briar.plan._models import ImplementationPlan, PlanCard


def collect_status(plan: ImplementationPlan, journal_store: Optional[Any]) -> Dict[str, Any]:
    """Walk plan + journal once and return a structured snapshot.

    Output shape (stable, used by the JSON/YAML renderers):
        {
          "plan": "<name>", "board": "<url>",
          "counts": {"done": N, "in_progress": N, "blocked": N, "pending": N},
          "done":        [{key,title,commit,pr_url,rationale}],
          "in_progress": [{key,title,started_at}],
          "blocked":     [{key,title,last_attempt,rationale}],
          "pending":     [{key,title,depends_on}],
        }
    """
    artifacts = _journal_artifacts(plan, journal_store)
    groups: Dict[str, List[PlanCard]] = {
        PlanCardStatus.DONE.value: [],
        PlanCardStatus.IN_PROGRESS.value: [],
        PlanCardStatus.BLOCKED.value: [],
        PlanCardStatus.PENDING.value: [],
    }
    for c in plan.cards:
        groups[c.status.value if hasattr(c.status, "value") else str(c.status)].append(c)

    def _done(c: PlanCard) -> Dict[str, Any]:
        a = artifacts.get(c.key, {})
        return {
            "key": c.key,
            "title": c.title,
            "commit": a.get("commit", ""),
            "pr_url": a.get("pr_url", ""),
            "rationale": a.get("completed_rationale", ""),
        }

    def _in_progress(c: PlanCard) -> Dict[str, Any]:
        a = artifacts.get(c.key, {})
        return {"key": c.key, "title": c.title, "started_at": a.get("started_at", "")}

    def _blocked(c: PlanCard) -> Dict[str, Any]:
        a = artifacts.get(c.key, {})
        return {
            "key": c.key,
            "title": c.title,
            "last_attempt": c.last_attempt_summary,
            "rationale": a.get("failed_rationale", ""),
        }

    def _pending(c: PlanCard) -> Dict[str, Any]:
        return {"key": c.key, "title": c.title, "depends_on": list(c.depends_on)}

    return {
        "plan": plan.name,
        "board": plan.board_url,
        "counts": {k: len(v) for k, v in groups.items()},
        "done": [_done(c) for c in groups[PlanCardStatus.DONE.value]],
        "in_progress": [_in_progress(c) for c in groups[PlanCardStatus.IN_PROGRESS.value]],
        "blocked": [_blocked(c) for c in groups[PlanCardStatus.BLOCKED.value]],
        "pending": [_pending(c) for c in groups[PlanCardStatus.PENDING.value]],
    }


def render_table(snapshot: Dict[str, Any]) -> str:
    """Human-readable grouped table. Mirrors the layout shown in the
    plan doc: counts header, then one block per status."""
    lines: List[str] = []
    lines.append(f"PLAN  {snapshot['plan']}   (board: {snapshot.get('board') or '(none)'})")
    lines.append("")
    counts = snapshot.get("counts", {})

    def _block(label: str, count_key: str, rows: List[Dict[str, Any]], formatter) -> None:
        lines.append(f"{label} ({counts.get(count_key, 0)})")
        if not rows:
            lines.append("  (none)")
        else:
            for r in rows:
                lines.append("  " + formatter(r))
        lines.append("")

    _block(
        "DONE",
        PlanCardStatus.DONE.value,
        snapshot.get("done", []),
        lambda r: f"{r['key']:<12} {r['title'][:50]:<50}"
        + (f" commit {r['commit'][:9]}" if r.get("commit") else "")
        + (f"  PR {r['pr_url']}" if r.get("pr_url") else ""),
    )
    _block(
        "IN PROGRESS",
        PlanCardStatus.IN_PROGRESS.value,
        snapshot.get("in_progress", []),
        lambda r: f"{r['key']:<12} {r['title'][:50]:<50}" + (f"  started {r['started_at']}" if r.get("started_at") else ""),
    )
    _block(
        "BLOCKED",
        PlanCardStatus.BLOCKED.value,
        snapshot.get("blocked", []),
        lambda r: f"{r['key']:<12} {r['title'][:50]:<50}"
        + (f"  \"{r['last_attempt'][:60]}\"" if r.get("last_attempt") else "")
        + (f"  ({r['rationale'][:60]})" if r.get("rationale") else ""),
    )
    _block(
        "PENDING",
        PlanCardStatus.PENDING.value,
        snapshot.get("pending", []),
        lambda r: f"{r['key']:<12} {r['title'][:50]:<50}" + (f"  [deps: {', '.join(r['depends_on'])}]" if r.get("depends_on") else ""),
    )
    return "\n".join(lines).rstrip() + "\n"


def _journal_artifacts(plan: ImplementationPlan, journal_store: Optional[Any]) -> Dict[str, Dict[str, str]]:
    """Walk every `plan.run` session targeting this plan and fold per-card
    artifacts (commit sha, PR URL, start time, rationale). Best-effort —
    a broken or empty journal yields an empty dict and the renderer
    still works."""
    out: Dict[str, Dict[str, str]] = {}
    if journal_store is None:
        return out
    target_prefix = f"{plan.name}@"
    try:
        refs = journal_store.list(command_prefix="plan.run")
    except Exception:  # noqa: BLE001
        return out
    for ref in refs:
        target = getattr(ref, "target", "") or ""
        if not target.startswith(target_prefix):
            continue
        try:
            session = journal_store.get(ref.session_id)
        except Exception:  # noqa: BLE001
            continue
        if session is None:
            continue
        for ev in session.decisions:
            choice = ev.choice
            key = str(ev.value or "") if choice.startswith("plan.run.card.") else ""
            if not key:
                continue
            slot = out.setdefault(key, {})
            if choice == "plan.run.card.start":
                slot.setdefault("started_at", getattr(ev, "timestamp", "") or "")
            elif choice == "plan.run.card.completed":
                slot["completed_rationale"] = ev.rationale or ""
                arts = dict(getattr(ev, "artifacts", {}) or {})
                for k in ("commit", "pr_url"):
                    if arts.get(k):
                        slot[k] = str(arts[k])
            elif choice == "plan.run.card.failed":
                slot["failed_rationale"] = ev.rationale or ""
    return out
