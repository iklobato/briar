"""swallow_errors — exception-eating decorator used by every external
adapter. Documents the broad-except behavior so a future fix that adds
selectivity is intentional, not silent."""

from __future__ import annotations

import logging

import pytest

from briar.decorators import swallow_errors


class TestSwallowErrors:
    def test_returns_value_on_success(self) -> None:
        @swallow_errors(default="default")
        def ok() -> str:
            return "ok"

        assert ok() == "ok"

    def test_returns_default_on_exception(self) -> None:
        @swallow_errors(default="fallback")
        def boom() -> str:
            raise RuntimeError("x")

        assert boom() == "fallback"

    @pytest.mark.parametrize("default", [None, [], {}, 0, False, "", "x"])
    def test_each_default_passed_through(self, default: object) -> None:
        @swallow_errors(default=default)
        def boom() -> object:
            raise RuntimeError("x")

        assert boom() == default

    def test_mutable_default_shared_documented_behavior(self) -> None:
        # KNOWN: the decorator returns the SAME default object every time.
        # If callers mutate it, state leaks across calls. This test documents
        # current behavior; a future fix should switch to a copy() and flip
        # this assertion.
        sentinel: list[int] = []

        @swallow_errors(default=sentinel)
        def boom() -> list[int]:
            raise RuntimeError("x")

        a = boom()
        b = boom()
        assert a is b is sentinel  # documents the aliasing

    @pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
    def test_base_exception_subclasses_propagate(self, exc_type: type[BaseException]) -> None:
        @swallow_errors(default=None)
        def boom() -> None:
            raise exc_type()

        with pytest.raises(exc_type):
            boom()

    def test_qualname_used_when_message_blank(self, caplog_briar) -> None:
        caplog_briar.set_level(logging.ERROR, logger="briar.decorators")

        @swallow_errors()
        def my_func() -> None:
            raise RuntimeError("x")

        my_func()
        assert any("my_func" in r.message for r in caplog_briar.records)

    def test_custom_message_used(self, caplog_briar) -> None:
        caplog_briar.set_level(logging.ERROR, logger="briar.decorators")

        @swallow_errors(message="custom-prefix")
        def boom() -> None:
            raise RuntimeError("x")

        boom()
        assert any("custom-prefix" in r.message for r in caplog_briar.records)

    def test_traceback_logged(self, caplog_briar) -> None:
        caplog_briar.set_level(logging.ERROR, logger="briar.decorators")

        @swallow_errors()
        def boom() -> None:
            raise RuntimeError("trace-me")

        boom()
        # log.exception() includes exc_info, which caplog captures
        records = [r for r in caplog_briar.records if r.exc_info]
        assert records, "expected at least one record with exc_info attached"

    def test_functools_wraps_preserves_name_and_doc(self) -> None:
        @swallow_errors()
        def original() -> int:
            """my doc"""
            return 1

        assert original.__name__ == "original"
        assert original.__doc__ == "my doc"

    def test_args_and_kwargs_forwarded(self) -> None:
        @swallow_errors(default="x")
        def add(a: int, b: int = 0) -> int:
            return a + b

        assert add(1, b=2) == 3
