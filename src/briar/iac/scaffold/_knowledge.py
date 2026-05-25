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

from briar.iac.scaffold.archetypes import AgentArchetype

log = logging.getLogger(__name__)


# Map extractor name → the `## <heading>` prefix the composer emits.
# Order doesn't matter — `prologue()` walks the archetype's declared
# order instead.
_EXTRACTOR_HEADINGS: Dict[str, str] = {
    "pr-archaeology": "PR archaeology",
    "active-work": "Active work",
    "github-deployments": "GitHub deployments",
    "codebase-conventions": "Codebase conventions",
    "aws-infra": "AWS infrastructure",
    "active-tickets": "Active tickets",
    "ticket-archaeology": "Ticket archaeology",
    "reviewer-profile": "Reviewer profiles",
    "code-hotspots": "Code hotspots",
    # JIT (task-scoped) sections — keys are the extractor names; the
    # heading prefix is what `FetchTicketContext.fetch` / FetchPrReviewContext
    # emit as their section.title. KnowledgeSplicer also looks these up
    # for archetype `consumes` ordering when the JIT section is present.
    "ticket-context": "Ticket context",
    "pr-review-context": "PR review context",
    "meeting-digest": "Meeting digest",
    "meeting-context": "Meeting context",
}


class KnowledgeSplicer:
    """Pulls every blob for a company from a `KnowledgeStore`, slices
    them into per-extractor sections by `## <heading>` markers, and
    returns the slices an archetype declares it consumes."""

    def __init__(self, store, company: str) -> None:
        self._store = store
        self._company = company
        self._sections: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        """Concatenate every blob whose name starts with `knowledge:<company>`
        and parse out `## <heading>` sections. The most-recent section for
        each extractor wins (later blob overwrites earlier — irrelevant in
        practice since each extractor only appears in one task's blob).

        Bulk-fetches via `get_many` so a Postgres-backed store opens one
        connection for the whole prologue instead of one per blob."""
        prefix = f"knowledge:{self._company}"
        refs = self._store.list(prefix=prefix)
        blobs = self._store.get_many([ref.name for ref in refs])
        for ref in refs:
            text = blobs.get(ref.name, "")
            if not text:
                continue
            for heading, body in self._parse_sections(text).items():
                self._sections[heading] = body
        log.debug("KnowledgeSplicer(%s): loaded %d sections", self._company, len(self._sections))

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
