"""Pagination helper — pure static methods over JSON-ish payload shapes.

Catches: type-vs-isinstance subtleties (subclass acceptance), implicit
wrapping of single dicts as singleton lists, never-raise contract."""

from __future__ import annotations

from collections import OrderedDict

import pytest
from hypothesis import given, strategies as st

from briar.pagination import items_of, looks_like_list


class TestItemsOf:
    def test_bare_list_returned_as_is(self) -> None:
        rows = [{"a": 1}, {"b": 2}]
        assert items_of(rows) is rows

    def test_dict_with_results_key_returns_results(self) -> None:
        assert items_of({"results": [{"a": 1}]}) == [{"a": 1}]

    def test_dict_without_results_wraps_in_singleton(self) -> None:
        page = {"id": "x"}
        assert items_of(page) == [page]

    def test_dict_with_results_none_wraps_dict_in_singleton(self) -> None:
        # `results=None` is not a list, so falls through to singleton wrap.
        page = {"results": None}
        assert items_of(page) == [page]

    @pytest.mark.parametrize("payload", [None, "string", 42, 3.14, (1, 2), b"bytes"])
    def test_non_list_non_dict_returns_empty(self, payload: object) -> None:
        assert items_of(payload) == []

    def test_ordered_dict_subclass_accepted(self) -> None:
        # `items_of` uses `isinstance`, so dict subclasses (OrderedDict,
        # and any mapping json.loads might hand back) are handled like a
        # plain dict — consistent with `looks_like_list`, which also uses
        # isinstance. A strict `type(x) is dict` here would make the two
        # helpers disagree on the same input.
        page = OrderedDict([("results", [{"a": 1}])])
        assert items_of(page) == [{"a": 1}]

    def test_empty_list_returned_empty(self) -> None:
        assert items_of([]) == []

    def test_dict_with_empty_results_returns_empty(self) -> None:
        assert items_of({"results": []}) == []


class TestLooksLikeList:
    def test_bare_list_true(self) -> None:
        assert looks_like_list([]) is True

    def test_dict_with_list_results_true(self) -> None:
        assert looks_like_list({"results": []}) is True

    def test_dict_without_results_false(self) -> None:
        assert looks_like_list({"id": "x"}) is False

    def test_dict_with_none_results_false(self) -> None:
        assert looks_like_list({"results": None}) is False

    def test_dict_with_dict_results_false(self) -> None:
        assert looks_like_list({"results": {}}) is False

    @pytest.mark.parametrize("payload", [None, "string", 42, (1,)])
    def test_non_list_non_dict_false(self, payload: object) -> None:
        assert looks_like_list(payload) is False


@pytest.mark.property
class TestPropertyTotal:
    @given(st.recursive(
        st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=20)),
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(st.text(min_size=1, max_size=10), children, max_size=5),
        ),
        max_leaves=20,
    ))
    def test_items_of_never_raises(self, payload: object) -> None:
        # Property: the function is total — always returns a list, never raises.
        result = items_of(payload)
        assert isinstance(result, list)

    @given(st.recursive(
        st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=20)),
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(st.text(min_size=1, max_size=10), children, max_size=5),
        ),
        max_leaves=20,
    ))
    def test_looks_like_list_never_raises(self, payload: object) -> None:
        assert isinstance(looks_like_list(payload), bool)
