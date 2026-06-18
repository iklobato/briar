"""Merge an extracted knowledge bundle into a project's ``CLAUDE.md`` so
Claude Code sessions can load the detail ON DEMAND.

``CLAUDE.md`` is auto-loaded into *every* session, so anything placed
there is a permanent per-session context cost. We therefore keep only a
short index in ``CLAUDE.md`` — the section titles plus a pointer to the
full detail file — and let the agent ``Read`` that file when a task
actually needs it. The index lives inside a managed marker block so a
re-run replaces just Briar's block and never disturbs hand-written
content around it.

Pure string composition only — no filesystem access. The command at the
edge owns reading/writing the files and stamps the timestamp, which
keeps this module trivially testable."""

from __future__ import annotations

from typing import List

from briar.extract.base import ExtractedSection

BEGIN_MARKER = "<!-- BEGIN briar-knowledge -->"
END_MARKER = "<!-- END briar-knowledge -->"


class ClaudeMdMerger:
    """Render and splice Briar's managed knowledge-index block."""

    @classmethod
    def index_block(
        cls,
        *,
        company: str,
        detail_path: str,
        sections: List[ExtractedSection],
        when: str,
    ) -> str:
        """The short block that lives inside ``CLAUDE.md``: a pointer to
        the full detail file plus the list of topics it covers, so a
        session knows when reading it is worthwhile."""
        lines: List[str] = [
            BEGIN_MARKER,
            f"## Project knowledge — {company} (Briar)",
            "",
            (f"_Extracted {when}. Full detail lives in `{detail_path}` — " "read that file on demand when a task needs context on any " "of these topics:_"),
            "",
        ]
        lines.extend(f"- {section.title}" for section in sections)
        lines.append(END_MARKER)
        return "\n".join(lines)

    @classmethod
    def merge(cls, *, existing: str, block: str) -> str:
        """Return ``CLAUDE.md`` content with Briar's block inserted or
        replaced. Everything outside the markers is preserved verbatim.

        A well-formed existing block (both markers, in order) is replaced
        in place. Otherwise the block is appended, separated from prior
        content by a blank line. A stray/malformed marker pair is treated
        as absent rather than guessed at — we never delete content we
        can't bound."""
        start = existing.find(BEGIN_MARKER)
        end = existing.find(END_MARKER)
        if start != -1 and end > start:
            end += len(END_MARKER)
            return existing[:start] + block + existing[end:]
        if existing.strip():
            return f"{existing.rstrip()}\n\n{block}\n"
        return f"{block}\n"
