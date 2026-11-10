"""User-filter (allowlist + blocklist) tests.

The dict-form `apply_user_filter` was deleted in Phase 13 — every
src caller uses the object-form `apply_user_filter_objs`. The
argparse-wiring helper survives + the source-template integration
test does too.
"""

from __future__ import annotations

import argparse
import unittest

from briar.extract._user_filter import (
    add_user_filter_arguments,
    apply_user_filter_objs,
)


class ApplyUserFilterObjsTests(unittest.TestCase):
    """The surviving object-form filter — used by every extractor that
    runs through normalised provider dataclasses."""

    def _items(self) -> list:
        # Lightweight stand-in for a `PullRequest`-shaped object —
        # only `.author` matters for filtering.
        Item = type("Item", (), {"__init__": lambda s, author: setattr(s, "author", author)})
        return [Item("alice"), Item("bob"), Item("bot[bot]"), Item("carol")]

    @staticmethod
    def _ns(**kw) -> argparse.Namespace:
        base = {"pr_authors_allow": [], "pr_authors_block": []}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_no_filter_returns_everything(self) -> None:
        items = self._items()
        self.assertEqual(apply_user_filter_objs(items, self._ns(), prefix="pr"), items)

    def test_authors_allow_intersects(self) -> None:
        kept = apply_user_filter_objs(self._items(), self._ns(pr_authors_allow=["alice", "bob"]), prefix="pr")
        self.assertEqual({i.author for i in kept}, {"alice", "bob"})

    def test_authors_block_subtracts(self) -> None:
        kept = apply_user_filter_objs(self._items(), self._ns(pr_authors_block=["bot[bot]"]), prefix="pr")
        self.assertNotIn("bot[bot]", {i.author for i in kept})

    def test_argparse_wiring(self) -> None:
        parser = argparse.ArgumentParser()
        add_user_filter_arguments(parser, prefix="pr")
        ns = parser.parse_args(
            [
                "--pr-authors-allow",
                "alice",
                "--pr-authors-allow",
                "bob",
                "--pr-authors-block",
                "bot[bot]",
            ]
        )
        self.assertEqual(ns.pr_authors_allow, ["alice", "bob"])
        self.assertEqual(ns.pr_authors_block, ["bot[bot]"])
        self.assertEqual(ns.pr_assignees_allow, [])


# The "runbook YAML schema" + "runbook executor flatten" tests were
# removed in the API-removal cut — both poked at fields (RunbookEntry,
# SourceEntry, _apply_github_source) that no longer exist now that the
# runbook schema is extract-only.


if __name__ == "__main__":
    unittest.main()
