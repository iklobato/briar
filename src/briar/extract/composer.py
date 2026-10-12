"""Compose a list of ExtractedSection into a single markdown blob.

Also emits a parallel JSON document with the same shape for
programmatic consumers. Static-only — no instance state."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from briar.extract.base import ExtractedSection


class KnowledgeComposer:
    """Markdown + JSON renderers for an extractor output bundle."""

    @classmethod
    def markdown(
        cls,
        *,
        company: str,
        sections: List[ExtractedSection],
    ) -> str:
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
            out.extend(cls._render_section(section, level=2))
        return "\n".join(out)

    @classmethod
    def json(
        cls,
        *,
        company: str,
        sections: List[ExtractedSection],
    ) -> str:
        payload: Dict[str, Any] = {
            "company": company,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections": [cls._section_to_dict(s) for s in sections],
        }
        return json.dumps(payload, indent=2, default=str)

    @classmethod
    def inventory(
        cls,
        *,
        company: str,
        sections: List[ExtractedSection],
    ) -> str:
        """Stable structured-inventory JSON for a section bundle.

        Unlike `json()`, this OMITS the volatile `generated_at`
        timestamp and sorts keys, so the output is byte-stable when the
        underlying resources are unchanged. That lets `put_if_changed`
        dedup it — the postgres history table then gains a row only when
        the estate actually drifts, turning the inventory blob into a
        change log rather than a per-run append."""
        payload: Dict[str, Any] = {
            "company": company,
            "sections": [cls._section_to_dict(s) for s in sections],
        }
        return json.dumps(payload, indent=2, sort_keys=True, default=str)

    @staticmethod
    def split_sections(text: str) -> Dict[str, str]:
        """Split a knowledge blob on `## ` headings, in order. Keys are the
        heading text before a spaced dash ("## PR archaeology - 1 repo(s)"
        gives "PR archaeology", an extractor's `heading`); values
        are the section text, heading line included. Text before the first
        `## ` (the blob's title block) is not returned."""
        sections: Dict[str, str] = {}
        current_heading = ""
        current_lines: List[str] = []
        for line in text.splitlines():
            if line.startswith("## "):
                if current_heading:
                    sections[current_heading] = "\n".join(current_lines).rstrip()
                raw = line[3:].strip()
                current_heading = re.split(r"\s+[\N{EM DASH}\N{EN DASH}-]\s+", raw, maxsplit=1)[0].strip()
                current_lines = [line]
                continue
            if current_heading:
                current_lines.append(line)
        if current_heading:
            sections[current_heading] = "\n".join(current_lines).rstrip()
        return sections

    @classmethod
    def merge_markdown(cls, *, company: str, existing: str, fresh: str, refreshed: Iterable[str]) -> str:
        """Rebuild a blob from `existing` with the sections under the
        `refreshed` headings taken from `fresh`. A refreshed heading that
        `fresh` lacks (its extractor ran but found nothing) is dropped;
        every other section of `existing` is kept, in place. New sections
        go at the end."""
        refreshed_headings = set(refreshed)
        new_sections = cls.split_sections(fresh)
        merged: Dict[str, str] = {}
        for heading, chunk in cls.split_sections(existing).items():
            if heading not in refreshed_headings:
                merged[heading] = chunk
            elif heading in new_sections:
                merged[heading] = new_sections[heading]
        for heading, chunk in new_sections.items():
            merged.setdefault(heading, chunk)
        header = cls.markdown(company=company, sections=[])
        return header + "\n" + "\n\n".join(merged.values()) + "\n"

    @classmethod
    def _render_section(
        cls,
        section: ExtractedSection,
        *,
        level: int,
    ) -> List[str]:
        chunk: List[str] = ["#" * level + f" {section.title}"]
        if section.body:
            chunk.append("")
            chunk.append(section.body)
        if section.subsections:
            for sub in section.subsections:
                chunk.append("")
                chunk.extend(cls._render_section(sub, level=level + 1))
        chunk.append("")
        return chunk

    @classmethod
    def _section_to_dict(cls, section: ExtractedSection) -> Dict[str, Any]:
        return {
            "title": section.title,
            "body": section.body,
            "data": section.data,
            "subsections": [cls._section_to_dict(s) for s in section.subsections],
        }


# Back-compat aliases.
render_markdown = KnowledgeComposer.markdown
render_json = KnowledgeComposer.json
