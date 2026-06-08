"""Extra branch coverage for `UserFilter.apply_objs` (_user_filter.py).

`tests/test_user_filter.py` already covers no-filter, allow-only, and
block-only. This file strengthens the uncovered branches:
- allow + block applied together (intersection of both constraints),
- the `getattr(item, "author", "") or ""` fallbacks (missing attr,
  None author, empty-string author),
- the `_matches` set-logic helper (allow-and-block on the same iterable).

No I/O — these are pure static helpers over normalised objects.
"""

from __future__ import annotations

import argparse
import unittest

import pytest

from briar.extract._user_filter import UserFilter, apply_user_filter_objs


def _item(author):
    obj = object.__new__(type("Item", (), {}))
    if author is not _MISSING:
        obj.author = author
    return obj


_MISSING = object()


def _ns(**kw):
    base = {"pr_authors_allow": [], "pr_authors_block": []}
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.boundary
class ApplyObjsBranchTests(unittest.TestCase):
    def test_allow_and_block_together(self) -> None:
        # allow keeps {alice, bob, carol}; block then removes bob.
        items = [_item("alice"), _item("bob"), _item("carol"), _item("dave")]
        kept = apply_user_filter_objs(
            items,
            _ns(pr_authors_allow=["alice", "bob", "carol"], pr_authors_block=["bob"]),
            prefix="pr",
        )
        self.assertEqual([i.author for i in kept], ["alice", "carol"])

    def test_missing_author_attr_treated_as_empty(self) -> None:
        # Item with no `.author` attribute → "" → excluded by a non-empty allow.
        items = [_item(_MISSING), _item("alice")]
        kept = apply_user_filter_objs(items, _ns(pr_authors_allow=["alice"]), prefix="pr")
        self.assertEqual([i.author for i in kept], ["alice"])

    def test_none_author_normalised_to_empty(self) -> None:
        items = [_item(None), _item("alice")]
        # block list that doesn't include "" must keep the None-author item.
        kept = apply_user_filter_objs(items, _ns(pr_authors_block=["spam"]), prefix="pr")
        self.assertEqual(len(kept), 2)

    def test_empty_author_blocked_when_block_contains_empty(self) -> None:
        # Explicit "" in block removes the empty/None-author item.
        items = [_item(None), _item("alice")]
        kept = apply_user_filter_objs(items, _ns(pr_authors_block=[""]), prefix="pr")
        self.assertEqual([i.author for i in kept], ["alice"])

    def test_namespace_without_keys_is_a_noop(self) -> None:
        # When the prefix flags are absent from the namespace, the filter
        # returns the list unchanged (vars().get → None → []).
        items = [_item("alice"), _item("bob")]
        out = apply_user_filter_objs(items, argparse.Namespace(), prefix="pr")
        self.assertEqual(out, items)


@pytest.mark.boundary
class MatchesHelperTests(unittest.TestCase):
    """Direct coverage of the `_matches` set-logic used by the (legacy)
    dict-form path — exercises both allow-miss and block-hit returns."""

    def test_no_constraints_matches(self) -> None:
        self.assertTrue(UserFilter._matches(["alice"], [], []))

    def test_allow_miss_returns_false(self) -> None:
        self.assertFalse(UserFilter._matches(["alice"], ["bob"], []))

    def test_allow_hit_returns_true(self) -> None:
        self.assertTrue(UserFilter._matches(["alice", "bob"], ["bob"], []))

    def test_block_hit_returns_false(self) -> None:
        self.assertFalse(UserFilter._matches(["alice", "spam"], [], ["spam"]))

    def test_falsy_values_are_dropped_from_actual(self) -> None:
        # The `{l for l in actual if l}` comprehension drops "" / None.
        self.assertFalse(UserFilter._matches(["", None], ["alice"], []))


if __name__ == "__main__":
    unittest.main()
