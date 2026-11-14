"""The knowledge of a company on disk: the single file, and one file per topic with an index beside it.

`serve` gives the agent the index, and the agent opens only the topics it needs, so the knowledge can
grow with every new source without every task reading all of it. The topic files are cut from the
merged text of the single file, never built a second time, so the two cannot differ. The single file
is exactly what it was before topic files existed: the README, the guide and `briar serve` of 1.x
users cite its path."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Set

from briar.extract.base import ExtractedSection
from briar.extract.composer import KnowledgeComposer, render_markdown
from briar.storage import KnowledgeRef, KnowledgeStore


class TopicFiles:
    """`<folder>/<topic>.md` for each `## ` section of the merged text, and `<folder>/index.md`."""

    INDEX = "index"
    ABOUT_CAP = 120
    BYTES_IN_KB = 1024

    def __init__(self, store: KnowledgeStore, folder: Path, now: Callable[[], datetime]) -> None:
        self._store = store
        self._folder = folder
        self._now = now

    def write(self, company: str, merged: str) -> None:
        """Write a file for each section and the index; remove the file of a section that is gone."""
        sections = KnowledgeComposer.split_sections(merged)
        names = self._names(sections)
        for heading, text in sections.items():
            self._put(names[heading], f"{text}\n")
        self._put(self.INDEX, self._index(company, sections, names))
        self._drop_other_than({*names.values(), self.INDEX})

    def _names(self, sections: Dict[str, str]) -> Dict[str, str]:
        """One file name per heading; a second heading with the same name gets `-2`, `-3`."""
        taken: Dict[str, str] = {}
        used: Set[str] = set()
        for heading in sections:
            base = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-") or "topic"
            name, count = base, 1
            while name in used:
                count += 1
                name = f"{base}-{count}"
            used.add(name)
            taken[heading] = name
        return taken

    def _index(self, company: str, sections: Dict[str, str], names: Dict[str, str]) -> str:
        written = self._now().strftime("%Y-%m-%d %H:%M")
        rows: List[str] = [f"| {heading} | {names[heading]}.md | {self._about(text)} | {self._size(text)} |" for heading, text in sections.items()]
        head = [
            f"# Knowledge index: {company}",
            "",
            f"Written {written} UTC. One file per topic: open only the topics you need. All of it in one file: [../{company}.md](../{company}.md).",
            "",
            "| Topic | File | About | Size |",
            "|---|---|---|---|",
        ]
        return "\n".join([*head, *rows]) + "\n"

    def _about(self, text: str) -> str:
        """The first sentence of prose after the heading, safe inside a table cell."""
        lines = [line.strip() for line in text.splitlines()[1:] if line.strip()]
        first = lines[0] if lines else ""
        sentence = first.split(". ")[0]
        shown = sentence if sentence == first else f"{sentence}."
        return shown.replace("|", "\\|")[: self.ABOUT_CAP]

    def _size(self, text: str) -> str:
        return f"{len(text.encode()) / self.BYTES_IN_KB:.1f} KB"

    def _put(self, name: str, text: str) -> None:
        # The store takes a path with a slash as it is; a blob name with a slash would leave the root.
        self._store.put(str(self._folder / f"{name}.md"), text, category="knowledge")

    def _drop_other_than(self, keep: Set[str]) -> None:
        for path in sorted(self._folder.glob("*.md")):
            if path.stem not in keep:
                self._store.delete(str(path))


class KnowledgeWriter:
    """The one writer of an extract run: merge the run into the single file, then write the topics."""

    def __init__(self, store: KnowledgeStore, topics: TopicFiles) -> None:
        self._store = store
        self._topics = topics

    def write(self, company: str, sections: Sequence[ExtractedSection], refreshed: Sequence[str]) -> KnowledgeRef:
        """Sections of extractors that did not run stay, in the single file and in the topics."""
        blob_name = f"knowledge:{company}"
        fresh = render_markdown(company=company, sections=list(sections))
        merged = KnowledgeComposer.merge_markdown(company=company, existing=self._store.get(blob_name), fresh=fresh, refreshed=refreshed)
        ref = self._store.put(blob_name, merged, category="knowledge")
        self._topics.write(company, merged)
        return ref
