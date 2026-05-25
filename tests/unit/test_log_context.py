"""Log-context — contextvars push/pop + record-prefixing filter.

Catches: leaked context between tests (autouse env_sandbox helps but
contextvars are independent), broken push/pop balance on exception,
filter rejecting records (it must always return True)."""

from __future__ import annotations

import contextlib
import logging

import pytest
from hypothesis import given, strategies as st

from briar.log_context import ContextFilter, current_context, log_context


class TestPushPop:
    def test_outside_context_returns_empty_dict(self) -> None:
        assert current_context() == {}

    def test_single_push_visible(self) -> None:
        with log_context(company="acme"):
            assert current_context() == {"company": "acme"}

    def test_pop_restores_outside_state(self) -> None:
        with log_context(company="acme"):
            pass
        assert current_context() == {}

    def test_nested_extends_parent(self) -> None:
        with log_context(company="acme"):
            with log_context(task="prfix"):
                assert current_context() == {"company": "acme", "task": "prfix"}

    def test_child_key_overrides_parent(self) -> None:
        with log_context(company="acme"):
            with log_context(company="other"):
                assert current_context()["company"] == "other"
            assert current_context()["company"] == "acme"

    def test_pop_on_exception(self) -> None:
        with contextlib.suppress(RuntimeError):
            with log_context(company="acme"):
                raise RuntimeError("boom")
        assert current_context() == {}

    @pytest.mark.parametrize("value", [42, 1.5, True, None, ["a"], {"k": "v"}])
    def test_non_string_values_stringified(self, value: object) -> None:
        with log_context(extra=value):
            assert current_context()["extra"] == str(value)

    def test_current_context_returns_copy(self) -> None:
        with log_context(company="acme"):
            ctx = current_context()
            ctx["mutated"] = "x"
            assert current_context() == {"company": "acme"}


class TestContextFilter:
    def _record(self, msg: str = "hello") -> logging.LogRecord:
        return logging.LogRecord("briar", logging.INFO, __file__, 0, msg, (), None)

    def test_no_context_returns_true_unchanged(self) -> None:
        f = ContextFilter()
        rec = self._record("hi")
        assert f.filter(rec) is True
        assert rec.msg == "hi"

    def test_ordered_keys_prepended_with_brackets(self) -> None:
        f = ContextFilter()
        with log_context(company="acme", task="prfix"):
            rec = self._record("hi")
            f.filter(rec)
        assert "[company=acme task=prfix]" in rec.msg
        assert rec.msg.endswith("hi")

    def test_canonical_order_company_task_extractor_shape_repo(self) -> None:
        f = ContextFilter()
        # Push in non-canonical order:
        with log_context(repo="x", company="acme", extractor="active", task="t", shape="s"):
            rec = self._record("hi")
            f.filter(rec)
        # Order: company, task, extractor, shape, repo
        prefix = rec.msg.split("]")[0] + "]"
        assert prefix.index("company") < prefix.index("task") < prefix.index("extractor") < prefix.index("shape") < prefix.index("repo")

    def test_unknown_keys_appended_after_canonical(self) -> None:
        f = ContextFilter()
        with log_context(company="acme", custom="x"):
            rec = self._record("hi")
            f.filter(rec)
        prefix = rec.msg.split("]")[0]
        assert prefix.index("company") < prefix.index("custom")

    def test_filter_always_returns_true(self) -> None:
        f = ContextFilter()
        with log_context(company="acme"):
            assert f.filter(self._record()) is True


@pytest.mark.property
class TestBalance:
    @given(depths=st.lists(st.text(min_size=1, max_size=10), min_size=1, max_size=8))
    def test_nested_pushes_pop_fully_after_exception_at_any_depth(self, depths: list[str]) -> None:
        with contextlib.suppress(RuntimeError):
            with contextlib.ExitStack() as stack:
                for i, key in enumerate(depths):
                    stack.enter_context(log_context(**{f"k{i}": key}))
                raise RuntimeError("boom")
        assert current_context() == {}
