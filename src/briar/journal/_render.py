"""Markdown rendering — pure function, no I/O.

Used by `FileSink` (writes the markdown to a file) and by the
`briar journal show` command (prints to stdout). Extracted because the
*format* is the same, even though the *destinations* differ. SRP: one
function, one job.

Notion / Slack / etc. sinks have their own per-API render — those are
content models, not "markdown rendered to a different surface", so
there is no shared abstraction across them. Don't add a `Renderer`
registry until a real third caller needs markdown."""

from __future__ import annotations

from typing import List

from briar.journal.models import DecisionEvent, Session


def render_markdown(session: Session) -> str:
    lines: List[str] = []
    title = session.command
    if session.target:
        title = f"{title} — {session.target}"
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **Session:** `{session.session_id}`")
    lines.append(f"- **Started:** {session.started_at}")
    if session.ended_at:
        lines.append(f"- **Ended:** {session.ended_at}")
    if session.metadata:
        lines.append("- **Metadata:**")
        for k, v in sorted(session.metadata.items()):
            lines.append(f"  - `{k}` = `{v}`")
    lines.append("")
    if not session.decisions:
        lines.append("_No decisions recorded._")
        return "\n".join(lines) + "\n"
    lines.append("## Decisions")
    lines.append("")
    for event in session.decisions:
        lines.extend(_render_event(event))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_event(event: DecisionEvent) -> List[str]:
    out: List[str] = []
    out.append(f"### `{event.choice}` — {_short_value(event.value)}")
    if event.rationale:
        out.append(f"_{event.rationale}_")
        out.append("")
    if event.alternatives:
        alt_str = ", ".join(f"`{_short_value(a)}`" for a in event.alternatives)
        out.append(f"- **Alternatives considered:** {alt_str}")
    if event.artifacts:
        out.append("- **Artifacts:**")
        for k, v in sorted(event.artifacts.items()):
            out.append(f"  - `{k}` → {v}")
    out.append(f"- _at {event.timestamp}_")
    return out


def _short_value(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_short_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{…}"
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "…"
