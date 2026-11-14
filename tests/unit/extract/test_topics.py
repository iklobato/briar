"""The topic files and the index beside the single knowledge file (decisions E4 and E16).

`KnowledgeWriter` is the one writer of a run: it writes the single file exactly as before,
then cuts the same merged text into one file per `## ` section and an index, so the two
outputs cannot differ."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pytest

from briar.extract.base import ExtractedSection
from briar.extract.composer import KnowledgeComposer, render_markdown
from briar.extract.topics import KnowledgeWriter, TopicFiles
from briar.storage import KnowledgeStore, make_store

WRITTEN_AT = datetime(2026, 11, 14, 12, 30, tzinfo=timezone.utc)


class Sections:
    """Extractor sections of the shape a real run produces."""

    def one(self, title: str, about: str, line: str) -> ExtractedSection:
        return ExtractedSection(title=title, body=f"{about}\n\n- {line}")

    def usual(self) -> List[ExtractedSection]:
        return [
            self.one(
                "PR archaeology — 1 repo(s)",
                "Patterns from the most recent merged PRs.",
                "merged PR sample: 100",
            ),
            self.one(
                "CI health — 1 repo(s)",
                "Pass rate and flaky workflows.",
                "pass rate: 0.93",
            ),
        ]


class Folder:
    """A knowledge folder in a temp dir, and what a reader finds in it."""

    def __init__(self, root: Path, writer: KnowledgeWriter) -> None:
        self.root = root
        self.writer = writer

    def write(self, sections: List[ExtractedSection], refreshed: List[str]) -> None:
        self.writer.write("myteam", sections, refreshed)

    def single(self) -> str:
        return (self.root / "knowledge" / "myteam.md").read_text()

    def topics(self) -> Path:
        return self.root / "knowledge" / "myteam"

    def topic(self, name: str) -> Path:
        return self.topics() / f"{name}.md"

    def index(self) -> str:
        return self.topic("index").read_text()


@pytest.fixture
def make() -> Sections:
    return Sections()


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    return make_store("file", file_root=tmp_path)


@pytest.fixture
def knowledge(tmp_path: Path, store: KnowledgeStore) -> Folder:
    return Folder(
        tmp_path,
        KnowledgeWriter(store, TopicFiles(store, tmp_path / "knowledge" / "myteam", lambda: WRITTEN_AT)),
    )


class TestSingleFileStaysAsBefore:
    def test_the_single_file_is_the_merge_of_the_run_with_nothing_added(self, knowledge: Folder, make: Sections) -> None:
        sections = make.usual()

        knowledge.write(sections, ["PR archaeology", "CI health"])

        fresh = render_markdown(company="myteam", sections=sections)
        assert KnowledgeComposer.split_sections(knowledge.single()) == KnowledgeComposer.split_sections(fresh)


class TestTopicFiles:
    def test_each_section_gets_a_file_with_exactly_its_text(self, knowledge: Folder, make: Sections) -> None:
        knowledge.write(make.usual(), ["PR archaeology", "CI health"])

        sections = KnowledgeComposer.split_sections(knowledge.single())
        assert knowledge.topic("pr-archaeology").read_text().rstrip() == sections["PR archaeology"]
        assert knowledge.topic("ci-health").read_text().rstrip() == sections["CI health"]

    def test_the_index_lists_every_topic_with_its_file_and_what_it_is_about(self, knowledge: Folder, make: Sections) -> None:
        knowledge.write(make.usual(), ["PR archaeology", "CI health"])

        index = knowledge.index()
        assert "| PR archaeology | pr-archaeology.md | Patterns from the most recent merged PRs. |" in index
        assert "| CI health | ci-health.md | Pass rate and flaky workflows. |" in index
        assert "2026-11-14 12:30 UTC" in index
        assert "../myteam.md" in index

    def test_a_section_kept_from_an_older_run_is_a_topic_too(self, knowledge: Folder, make: Sections) -> None:
        knowledge.write(make.usual(), ["PR archaeology", "CI health"])

        knowledge.write(
            [make.one("PR archaeology — 1 repo(s)", "Newer.", "merged PR sample: 7")],
            ["PR archaeology"],
        )

        assert "pass rate: 0.93" in knowledge.topic("ci-health").read_text()
        assert "merged PR sample: 7" in knowledge.topic("pr-archaeology").read_text()

    def test_a_topic_whose_section_is_gone_loses_its_file(self, knowledge: Folder, make: Sections) -> None:
        knowledge.write(make.usual(), ["PR archaeology", "CI health"])

        knowledge.write([], ["CI health"])

        assert not knowledge.topic("ci-health").exists()
        assert knowledge.topic("pr-archaeology").exists()
        assert "ci-health.md" not in knowledge.index()

    def test_a_pipe_in_the_first_line_does_not_break_the_index_table(self, knowledge: Folder, make: Sections) -> None:
        knowledge.write([make.one("Odd — 1", "a | b is tricky", "x")], ["Odd"])

        assert "a \\| b is tricky" in knowledge.index()

    def test_two_headings_with_the_same_slug_keep_both_files(self, knowledge: Folder, make: Sections) -> None:
        both = [
            make.one("CI health — 1", "One.", "a"),
            make.one("CI  health — 2", "Two.", "b"),
        ]

        knowledge.write(both, ["CI health", "CI  health"])

        assert sorted(path.name for path in knowledge.topics().glob("ci-health*.md")) == ["ci-health-2.md", "ci-health.md"]
