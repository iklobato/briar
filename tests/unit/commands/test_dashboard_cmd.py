"""`briar dashboard` — boot the read-only monitoring dashboard.

The HTTP server and collector registry are mocked at the seam
``commands/dashboard.py`` imports them from. We assert the server is
constructed with the right bind/port, that paths + collectors are wired in,
and that `--once` renders to stdout without ever calling `serve()`.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def wired(mocker):
    """Patch the dashboard collaborators and return handles to assert on."""
    fake_server = mocker.MagicMock()
    fake_server.started_at = 123.0
    fake_server.render_index.return_value = "<html>DASHBOARD</html>"
    server_ctor = mocker.patch("briar.commands.dashboard.DashboardServer", return_value=fake_server)
    from_paths = mocker.patch("briar.commands.dashboard.CollectorRegistry.from_paths", return_value=["COLLECTOR"])

    return mocker.MagicMock(server=fake_server, server_ctor=server_ctor, from_paths=from_paths)


class TestServe:
    def test_default_bind_and_port(self, cli, wired) -> None:
        result = cli("dashboard")
        assert result.code == 0
        assert wired.server_ctor.call_args.kwargs == {"host": "127.0.0.1", "port": 8080}
        wired.server.set_collectors.assert_called_once_with(["COLLECTOR"])
        wired.server.serve.assert_called_once_with()

    def test_custom_host_and_port_forwarded(self, cli, wired) -> None:
        result = cli("dashboard", "--host", "0.0.0.0", "--port", "9001")
        assert result.code == 0
        assert wired.server_ctor.call_args.kwargs == {"host": "0.0.0.0", "port": 9001}

    def test_paths_built_from_flags(self, cli, wired) -> None:
        cli("dashboard", "--examples", "/tmp/ex", "--log-file", "/tmp/s.log", "--repo-path", "/tmp/repo", "--disk-path", "/data")
        paths, dash = wired.from_paths.call_args.args
        assert paths.examples_dir == Path("/tmp/ex")
        assert paths.log_path == Path("/tmp/s.log")
        assert paths.repo_path == Path("/tmp/repo")
        assert paths.disk_path == Path("/data")
        assert dash.started_at == 123.0

    def test_once_renders_and_does_not_serve(self, cli, wired) -> None:
        result = cli("dashboard", "--once")
        assert result.code == 0
        assert "DASHBOARD" in result.out
        wired.server.render_index.assert_called_once_with()
        wired.server.serve.assert_not_called()


class TestArgs:
    def test_bad_port_usage_error(self, cli, wired) -> None:
        result = cli("dashboard", "--port", "not-an-int")
        assert result.code == 2  # argparse type=int rejects it

    def test_removed_flags_are_rejected(self, cli, wired) -> None:
        # The content/secrets/journal flags were dropped with their collectors.
        for flag in ("--knowledge-store", "--journal-store", "--secrets-file", "--du-path"):
            result = cli("dashboard", flag, "x")
            assert result.code == 2, f"{flag} should no longer be accepted"
