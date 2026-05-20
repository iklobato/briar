"""Compose a list of ExtractedSection into a single markdown blob.

Also emits a parallel JSON document with the same shape for
programmatic consumers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from briar.extract.base import ExtractedSection


def render_markdown(
    *,
    company: str,
    sections: List[ExtractedSection],
) -> str:
    """Header + each section + nested sub-sections."""
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    out: List[str] = [
        f"# Briar knowledge base — {company}",
        f"_generated {when}_",
        "",
        (
            "This file is the agentic context blob for the **"
            f"{company}** workspace. Agents read it on every run; the "
            "extractor refreshes it on its own cadence (see runbook)."
        ),
        "",
    ]
    for section in sections:
        out.extend(_render_section(section, level=2))
    return "\n".join(out)


def _render_section(section: ExtractedSection, *, level: int) -> List[str]:
    chunk: List[str] = ["#" * level + f" {section.title}"]
    if section.body:
        chunk.append("")
        chunk.append(section.body)
    if section.subsections:
        for sub in section.subsections:
            chunk.append("")
            chunk.extend(_render_section(sub, level=level + 1))
    chunk.append("")
    return chunk


def render_json(
    *,
    company: str,
    sections: List[ExtractedSection],
) -> str:
    payload: Dict[str, Any] = {
        "company": company,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": [_section_to_dict(s) for s in sections],
    }
    return json.dumps(payload, indent=2, default=str)


def _section_to_dict(section: ExtractedSection) -> Dict[str, Any]:
    return {
        "title": section.title,
        "body": section.body,
        "data": section.data,
        "subsections": [_section_to_dict(s) for s in section.subsections],
    }
