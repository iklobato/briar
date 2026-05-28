"""KnowledgeSplicer — pulls per-extractor sections from the configured
`KnowledgeStore` and renders the archetype's `consumes` list into a
system_prompt prologue.

The composer calls `KnowledgeSplicer.prologue(archetype)` at scaffold
time. Resulting prompt is self-contained: a downstream agent runtime
doesn't have to know how to query Postgres — the knowledge is already
baked into `agent.system_prompt`."""

from __future__ import annotations

import logging
import re
from typing import Dict, List

from briar.extract import EXTRACTORS, TASK_SCOPED_EXTRACTORS
from briar.iac.scaffold.archetypes import AgentArchetype

log = logging.getLogger(__name__)


# Map extractor name → the `## <heading>` prefix the composer emits.
# Derived from the extractor registries: each KnowledgeExtractor (and
# TaskScopedExtractor) declares its own `heading` ClassVar. Adding a
# new extractor with a heading automatically participates; an extractor
# with empty heading is omitted (JIT-only or not splicer-bound).
_EXTRACTOR_HEADINGS: Dict[str, str] = {
    name: ext.heading
    for name, ext in {**EXTRACTORS, **TASK_SCOPED_EXTRACTORS}.items()
    if ext.heading
}


class KnowledgeSplicer:
    """Pulls every blob for a company from a `KnowledgeStore`, slices
    them into per-extractor sections by `## <heading>` markers, and
    returns the slices an archetype declares it consumes.

    Construct via ``KnowledgeSplicer.from_store(store, company)`` —
    the previous ``__init__`` did the network/Postgres fetch eagerly,
    which made the class impossible to construct in unit tests without
    a live store. The plain ``__init__`` now only takes pre-loaded
    sections; ``from_store`` is the I/O entry point."""

    def __init__(self, company: str, sections: Dict[str, str]) -> None:
        self._company = company
        self._sections = sections

    @classmethod
    def from_store(cls, store, company: str) -> "KnowledgeSplicer":
        """Concatenate every blob whose name starts with `knowledge:<company>`
        and parse out `## <heading>` sections. The most-recent section for
        each extractor wins (later blob overwrites earlier — irrelevant in
        practice since each extractor only appears in one task's blob).

        Bulk-fetches via `get_many` so a Postgres-backed store opens one
        connection for the whole prologue instead of one per blob."""
        prefix = f"knowledge:{company}"
        refs = store.list(prefix=prefix)
        blobs = store.get_many([ref.name for ref in refs])
        sections: Dict[str, str] = {}
        for ref in refs:
            text = blobs.get(ref.name, "")
            if not text:
                continue
            for heading, body in cls._parse_sections(text).items():
                sections[heading] = body
        log.debug("KnowledgeSplicer(%s): loaded %d sections", company, len(sections))
        return cls(company, sections)

    @staticmethod
    def _parse_sections(text: str) -> Dict[str, str]:
        """Split a knowledge blob on `## ` headings. Returns a dict
        keyed by the heading text (before any em-dash continuation)."""
        sections: Dict[str, str] = {}
        current_heading = ""
        current_lines: List[str] = []
        for line in text.splitlines():
            if line.startswith("## "):
                if current_heading:
                    sections[current_heading] = "\n".join(current_lines).rstrip()
                # Heading: take everything after `## `, normalise by
                # splitting on em-dash so "## PR archaeology — 1 repo(s)"
                # becomes key "PR archaeology".
                raw = line[3:].strip()
                head = re.split(r"\s+[—–-]\s+", raw, maxsplit=1)[0].strip()
                current_heading = head
                current_lines = [line]
                continue
            if current_heading:
                current_lines.append(line)
        if current_heading:
            sections[current_heading] = "\n".join(current_lines).rstrip()
        return sections

    def section(self, extractor_name: str) -> str:
        """Return the markdown chunk for one extractor, or empty
        string if the company's knowledge doesn't have it."""
        heading = _EXTRACTOR_HEADINGS.get(extractor_name, "")
        return self._sections.get(heading, "") if heading else ""

    def prologue(self, archetype: AgentArchetype) -> str:
        """Concatenate every consumed extractor's section into a
        single markdown block, in the archetype's declared order.
        Skip extractors whose section is missing (the archetype's
        prompt already names them, so the agent can detect absence)."""
        chunks: List[str] = []
        for extractor_name in archetype.consumes:
            body = self.section(extractor_name)
            if not body:
                log.debug("KnowledgeSplicer(%s): no section for extractor %s", self._company, extractor_name)
                continue
            chunks.append(body)
        if not chunks:
            return ""
        header = (
            f"# Gathered knowledge for {self._company}\n"
            "_Extracted by briar; the archetype's prompt above lists "
            "the order in which you should consult these sections._\n"
        )
        return header + "\n\n" + "\n\n".join(chunks) + "\n"
