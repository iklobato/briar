"""Tests for the CLAUDE.md knowledge-index merge.

The merger is pure string composition; the command-level test exercises
the file I/O (detail file written locally, index spliced into CLAUDE.md)
and the non-destructive marker-block behaviour."""

from __future__ import annotations

import argparse
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from briar.commands.extract import CommandExtract, _company_slug
from briar.extract.base import ExtractedSection
from briar.extract.claude_md import BEGIN_MARKER, END_MARKER, ClaudeMdMerger

_SECTIONS = [
    ExtractedSection(title="CI health", body="pass rate 92%"),
    ExtractedSection(title="PR hygiene", body="median 84 LOC"),
]


class IndexBlockTests(unittest.TestCase):
    def _block(self) -> str:
        return ClaudeMdMerger.index_block(
            company="Acme",
            detail_path=".briar/knowledge/acme.md",
            sections=_SECTIONS,
            when="2026-06-18T13:00Z",
        )

    def test_block_is_marker_bounded(self) -> None:
        block = self._block()
        self.assertTrue(block.startswith(BEGIN_MARKER))
        self.assertTrue(block.rstrip().endswith(END_MARKER))

    def test_block_points_at_detail_and_lists_titles(self) -> None:
        block = self._block()
        self.assertIn(".briar/knowledge/acme.md", block)
        self.assertIn("on demand", block)
        self.assertIn("- CI health", block)
        self.assertIn("- PR hygiene", block)

    def test_block_omits_section_bodies(self) -> None:
        # Index stays light: titles only, never the detail bodies.
        block = self._block()
        self.assertNotIn("pass rate 92%", block)


class MergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.block = ClaudeMdMerger.index_block(
            company="Acme",
            detail_path=".briar/knowledge/acme.md",
            sections=_SECTIONS,
            when="2026-06-18T13:00Z",
        )

    def test_append_into_existing_preserves_handwritten_content(self) -> None:
        existing = "# My project\n\nHand-written rules.\n"
        merged = ClaudeMdMerger.merge(existing=existing, block=self.block)
        self.assertIn("Hand-written rules.", merged)
        self.assertIn(BEGIN_MARKER, merged)

    def test_empty_existing_yields_just_the_block(self) -> None:
        merged = ClaudeMdMerger.merge(existing="", block=self.block)
        self.assertEqual(merged.strip(), self.block.strip())

    def test_rerun_replaces_block_without_duplicating(self) -> None:
        first = ClaudeMdMerger.merge(existing="# Top\n", block=self.block)
        fresh = ClaudeMdMerger.index_block(
            company="Acme",
            detail_path=".briar/knowledge/acme.md",
            sections=[ExtractedSection(title="Stale PRs", body="3 idle")],
            when="2026-06-19T09:00Z",
        )
        second = ClaudeMdMerger.merge(existing=first, block=fresh)
        self.assertEqual(second.count(BEGIN_MARKER), 1)
        self.assertEqual(second.count(END_MARKER), 1)
        self.assertIn("- Stale PRs", second)
        self.assertNotIn("- CI health", second)
        self.assertIn("# Top", second)

    def test_malformed_markers_are_not_treated_as_a_block(self) -> None:
        # END before BEGIN must not delete the content between them.
        existing = f"keep-a\n{END_MARKER}\nkeep-b\n{BEGIN_MARKER}\nkeep-c\n"
        merged = ClaudeMdMerger.merge(existing=existing, block=self.block)
        self.assertIn("keep-a", merged)
        self.assertIn("keep-b", merged)
        self.assertIn("keep-c", merged)


class CompanySlugTests(unittest.TestCase):
    def test_spaces_and_punctuation_collapse_to_single_dash(self) -> None:
        self.assertEqual(_company_slug("Acme Inc."), "acme-inc")

    def test_empty_falls_back(self) -> None:
        self.assertEqual(_company_slug("!!!"), "knowledge")


class MergeCommandIoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_cwd = os.getcwd()
        self._tmp = TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self) -> None:
        os.chdir(self._prev_cwd)
        self._tmp.cleanup()

    def _args(self) -> argparse.Namespace:
        return argparse.Namespace(company="Acme Inc.", claude_md_path="./CLAUDE.md")

    def test_writes_detail_file_and_merges_index(self) -> None:
        CommandExtract()._merge_claude_md(self._args(), _SECTIONS)

        detail = Path(".briar/knowledge/acme-inc.md")
        self.assertTrue(detail.exists())
        self.assertIn("pass rate 92%", detail.read_text())

        claude_md = Path("./CLAUDE.md").read_text()
        self.assertIn(BEGIN_MARKER, claude_md)
        self.assertIn(".briar/knowledge/acme-inc.md", claude_md)

    def test_merge_keeps_existing_claude_md(self) -> None:
        Path("./CLAUDE.md").write_text("# Existing\n\nkeep me\n")
        CommandExtract()._merge_claude_md(self._args(), _SECTIONS)
        claude_md = Path("./CLAUDE.md").read_text()
        self.assertIn("keep me", claude_md)
        self.assertEqual(claude_md.count(BEGIN_MARKER), 1)


if __name__ == "__main__":
    unittest.main()
