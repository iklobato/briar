"""KnowledgeSplicer tests — uses a `StoreFile` backend with a tempdir
so no PG connection is required."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from briar.iac.scaffold._knowledge import KnowledgeSplicer
from briar.iac.scaffold.archetypes import ARCHETYPES
from briar.storage import make_store


_ACME_BLOB = """\
# Briar knowledge base — acme
_generated 2026-05-20T00:00Z_

## PR archaeology — 1 repo(s)
- merged PRs: 42
- top reviewers: alice(10), bob(7)

## Active work — 1 repo(s)
- #123 open PR
- #124 open PR

## Codebase conventions — 1 repo(s)
- test_runner: pytest
- linter: ruff
"""

_ACME_PRFIX = """\
# Briar knowledge base — acme
_generated 2026-05-20T00:01Z_

## Active work — 1 repo(s)
- #200 fresh PR for prfix
"""


class KnowledgeSplicerTests(unittest.TestCase):
    def test_section_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = make_store("file", file_root=Path(td))
            store.put("knowledge:acme", _ACME_BLOB)
            splicer = KnowledgeSplicer.from_store(store, "acme")

        pr_arch = splicer.section("pr-archaeology")
        self.assertIn("## PR archaeology", pr_arch)
        self.assertIn("alice(10)", pr_arch)
        # Should NOT contain another section's body.
        self.assertNotIn("Codebase conventions", pr_arch)

        self.assertIn("Codebase conventions", splicer.section("codebase-conventions"))
        # An extractor that isn't in the blob returns empty.
        self.assertEqual(splicer.section("aws-infra"), "")

    def test_prologue_follows_archetype_order(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = make_store("file", file_root=Path(td))
            store.put("knowledge:acme", _ACME_BLOB)
            splicer = KnowledgeSplicer.from_store(store, "acme")
            engineer = ARCHETYPES["engineer"]
            prologue = splicer.prologue(engineer)

        # All consumed extractors that exist in the blob should appear,
        # in archetype-declared order: codebase-conventions → active-work
        # → pr-archaeology → ...
        i_cc = prologue.find("Codebase conventions")
        i_aw = prologue.find("Active work")
        i_pra = prologue.find("PR archaeology")
        self.assertGreater(i_cc, -1)
        self.assertGreater(i_aw, i_cc)
        self.assertGreater(i_pra, i_aw)
        # aws-infra isn't in the blob, prologue must skip it without error
        self.assertNotIn("AWS infrastructure", prologue)
        # Header is present.
        self.assertIn("Gathered knowledge for acme", prologue)

    def test_pr_fixer_consumes_only_its_declared_set(self) -> None:
        """pr-fixer's consumes shifted: it now prioritises the JIT
        pr-review-context + reviewer-profile + code-hotspots over the
        old (active-work, pr-archaeology, codebase-conventions) trio.
        AWS infra + GitHub deployments are still excluded."""
        with tempfile.TemporaryDirectory() as td:
            store = make_store("file", file_root=Path(td))
            store.put("knowledge:acme", _ACME_BLOB)
            splicer = KnowledgeSplicer.from_store(store, "acme")
            prologue = splicer.prologue(ARCHETYPES["pr-fixer"])

        self.assertIn("Active work", prologue)
        self.assertIn("Codebase conventions", prologue)
        # pr-fixer does not consume AWS or deployments — they shouldn't
        # be in the prologue even if they were in the blob.
        self.assertNotIn("AWS infrastructure", prologue)
        self.assertNotIn("GitHub deployments", prologue)
        # pr-archaeology was dropped from pr-fixer's consumes in favour
        # of the more-actionable reviewer-profile + code-hotspots.
        self.assertNotIn("PR archaeology", prologue)

    def test_multi_blob_merge(self) -> None:
        """Per-task blobs (e.g. `knowledge:acme.prfix`) get merged in
        with the main blob — later writes win, missing sections come
        from the main blob."""
        with tempfile.TemporaryDirectory() as td:
            store = make_store("file", file_root=Path(td))
            store.put("knowledge:acme", _ACME_BLOB)
            store.put("knowledge:acme.prfix", _ACME_PRFIX)
            splicer = KnowledgeSplicer.from_store(store, "acme")

        active = splicer.section("active-work")
        # The prfix blob has the newer Active work section — it should
        # win over the main blob's older version.
        self.assertIn("#200 fresh PR for prfix", active)
        # PR archaeology only exists in the main blob — should still
        # be retrieved correctly.
        self.assertIn("alice(10)", splicer.section("pr-archaeology"))

    def test_empty_company_returns_empty_prologue(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = make_store("file", file_root=Path(td))
            splicer = KnowledgeSplicer.from_store(store, "no-such-company")
            self.assertEqual(splicer.prologue(ARCHETYPES["engineer"]), "")


if __name__ == "__main__":
    unittest.main()
