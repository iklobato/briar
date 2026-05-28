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


# ---------------------------------------------------------------------------
# Source template — does the GitHub source emit the filters?
# ---------------------------------------------------------------------------


class SourceGithubFiltersTests(unittest.TestCase):
    def test_filters_appear_in_source_config(self) -> None:
        from briar.iac.scaffold.sources.github import SourceGithub

        ns = argparse.Namespace(
            owner="alice",
            repo="widgets",
            auth_mode="pat",
            github_secret_id="some-uuid",
            github_authors_allow=["alice", "bob"],
            github_authors_block=["dependabot[bot]"],
            github_assignees_allow=[],
            github_assignees_block=["bot"],
        )
        src = SourceGithub().build_source(ns, key_prefix="t")
        self.assertEqual(src["config"]["authors_allow"], ["alice", "bob"])
        self.assertEqual(src["config"]["authors_block"], ["dependabot[bot]"])
        self.assertNotIn("assignees_allow", src["config"])  # empty omitted
        self.assertEqual(src["config"]["assignees_block"], ["bot"])

    def test_no_filters_emits_clean_config(self) -> None:
        from briar.iac.scaffold.sources.github import SourceGithub

        ns = argparse.Namespace(
            owner="alice",
            repo="widgets",
            auth_mode="pat",
            github_secret_id="x",
            github_authors_allow=[],
            github_authors_block=[],
            github_assignees_allow=[],
            github_assignees_block=[],
        )
        src = SourceGithub().build_source(ns, key_prefix="t")
        # No user-filter keys at all when everything is empty.
        for k in (
            "authors_allow",
            "authors_block",
            "assignees_allow",
            "assignees_block",
        ):
            self.assertNotIn(k, src["config"])


# The "runbook YAML schema" + "runbook executor flatten" tests were
# removed in the API-removal cut — both poked at fields (RunbookEntry,
# SourceEntry, _apply_github_source) that no longer exist now that the
# runbook schema is extract-only.


if __name__ == "__main__":
    unittest.main()
