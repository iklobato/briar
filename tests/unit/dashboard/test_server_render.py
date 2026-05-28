"""DashboardServer render isolation — collector failures must not 500 the page."""

from __future__ import annotations

from briar.dashboard.server import DashboardServer


class _FakeCollector:
    def __init__(self, name: str, result=None, fail: bool = False) -> None:
        self.name = name
        self._result = result if result is not None else {}
        self._fail = fail

    def collect(self):
        if self._fail:
            raise RuntimeError(f"{self.name} broke")
        return self._result


# Template requires these keys. Provide empty defaults via stub collectors.
_TEMPLATE_KEYS = [
    "system", "companies", "knowledge", "knowledge_aggregates", "schedules",
    "schedule_calendar", "gh_stats", "scheduler_log", "scheduler_process",
    "dashboard_process", "deploy", "connectivity", "cycle_outcomes",
    "commands", "extractors", "archetypes", "aws_services", "language_detectors",
    "source_templates", "trigger_templates", "workflow_shapes", "secrets",
    "plans", "journal_sessions",
]


def _stub_collectors(failing: set[str] = frozenset()) -> list[_FakeCollector]:
    return [_FakeCollector(name, result={}, fail=(name in failing)) for name in _TEMPLATE_KEYS]


class TestRenderIsolation:
    def test_one_collector_failure_does_not_abort_render(self, caplog_briar) -> None:
        server = DashboardServer()
        server.set_collectors(_stub_collectors(failing={"knowledge"}))
        # Contract: a single collector failure must NOT 500 the page —
        # render returns HTML and the failure is logged. (The resilient
        # Undefined in the Jinja env degrades the failed section's fields
        # to blanks rather than raising mid-render.)
        html = server.render_index()
        assert isinstance(html, str) and html
        assert any("knowledge" in r.message for r in caplog_briar.records)

    def test_all_collectors_failing_still_renders(self) -> None:
        # The ultimate isolation check: even if EVERY collector throws,
        # the page renders rather than 500ing. Guards against a
        # regression that removes the env's resilient Undefined.
        server = DashboardServer()
        server.set_collectors(_stub_collectors(failing=set(_TEMPLATE_KEYS)))
        html = server.render_index()
        assert isinstance(html, str) and html

    def test_render_index_calls_every_collector(self) -> None:
        server = DashboardServer()
        collectors = _stub_collectors()
        called: list[str] = []
        for c in collectors:
            original = c.collect

            def _wrap(orig=original, name=c.name):
                def inner():
                    called.append(name)
                    return orig()
                return inner

            c.collect = _wrap()
        server.set_collectors(collectors)
        html = server.render_index()
        assert isinstance(html, str)
        # Every collector must have run, and the page rendered.
        assert set(called) == set(_TEMPLATE_KEYS)


class TestSelfStats:
    def test_request_count_starts_zero(self) -> None:
        server = DashboardServer()
        assert server.request_count() == 0

    def test_increment_thread_safe(self) -> None:
        server = DashboardServer()
        for _ in range(100):
            server.increment_requests()
        assert server.request_count() == 100
