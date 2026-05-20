"""log_context — verifies the ContextVar push/pop semantics and that
the ContextFilter prefixes log records with the active bindings."""

from __future__ import annotations

import logging
import unittest

from briar.log_context import ContextFilter, current_context, log_context


class LogContextTests(unittest.TestCase):
    def test_nested_contexts_extend(self) -> None:
        with log_context(company="acme"):
            self.assertEqual(current_context(), {"company": "acme"})
            with log_context(task="prfix"):
                self.assertEqual(current_context(), {"company": "acme", "task": "prfix"})
                with log_context(extractor="active-work"):
                    self.assertEqual(
                        current_context(),
                        {"company": "acme", "task": "prfix", "extractor": "active-work"},
                    )

    def test_context_unwinds_after_block(self) -> None:
        with log_context(company="acme"):
            self.assertEqual(current_context(), {"company": "acme"})
        self.assertEqual(current_context(), {})

    def test_context_unwinds_on_exception(self) -> None:
        try:
            with log_context(company="acme"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertEqual(current_context(), {})

    def test_filter_prepends_known_keys_in_order(self) -> None:
        record = logging.LogRecord(
            name="briar.test",
            level=logging.INFO,
            pathname="x.py",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        flt = ContextFilter()
        with log_context(task="prfix", company="acme", extractor="active-work"):
            flt.filter(record)
        # The declared `_ORDER` is (company, task, extractor, ...) so the
        # prefix should respect that even though we pushed them out of order.
        self.assertEqual(record.msg, "[company=acme task=prfix extractor=active-work] hello")

    def test_filter_no_op_when_context_empty(self) -> None:
        record = logging.LogRecord(
            name="briar.test",
            level=logging.INFO,
            pathname="x.py",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        flt = ContextFilter()
        flt.filter(record)
        self.assertEqual(record.msg, "hello")


if __name__ == "__main__":
    unittest.main()
