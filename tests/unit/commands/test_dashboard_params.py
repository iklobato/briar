"""`briar dashboard` — parametric per-flag EFFECT assertions.

Companion to ``test_dashboard_cmd.py``. Pins each surviving flag to an
observable effect: bind flags reach the server constructor, and every path
flag lands on the ``DashboardPaths`` struct the collector registry is built
from. Collaborators are mocked at the ``commands/dashboard.py`` import seam,
so a flag the command fails to thread through makes the assertion FAIL.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def wired(mocker):
    fake_server = mocker.MagicMock()
    fake_server.started_at = 123.0
    fake_server.render_index.return_value = "<html>DASHBOARD</html>"
    server_ctor = mocker.patch("briar.commands.dashboard.DashboardServer", return_value=fake_server)
    from_paths = mocker.patch("briar.commands.dashboard.CollectorRegistry.from_paths", return_value=["COLLECTOR"])
    return mocker.MagicMock(server=fake_server, server_ctor=server_ctor, from_paths=from_paths)


def _paths(wired):
    paths, _dash = wired.from_paths.call_args.args
    return paths


class TestBindDefaults:
    def test_host_default_is_loopback(self, cli, wired) -> None:
        assert cli("dashboard").code == 0
        assert wired.server_ctor.call_args.kwargs["host"] == "127.0.0.1"

    def test_port_default_is_8080(self, cli, wired) -> None:
        assert cli("dashboard").code == 0
        assert wired.server_ctor.call_args.kwargs["port"] == 8080

    def test_host_flag_forwarded(self, cli, wired) -> None:
        assert cli("dashboard", "--host", "0.0.0.0").code == 0
        assert wired.server_ctor.call_args.kwargs["host"] == "0.0.0.0"

    def test_port_flag_forwarded_as_int(self, cli, wired) -> None:
        assert cli("dashboard", "--port", "9090").code == 0
        assert wired.server_ctor.call_args.kwargs["port"] == 9090


class TestPathFlags:
    def test_examples_flag_sets_examples_dir(self, cli, wired) -> None:
        assert cli("dashboard", "--examples", "/srv/runbooks").code == 0
        assert _paths(wired).examples_dir == Path("/srv/runbooks")

    def test_examples_default(self, cli, wired) -> None:
        assert cli("dashboard").code == 0
        assert _paths(wired).examples_dir == Path("./examples")

    def test_log_file_flag_sets_log_path(self, cli, wired) -> None:
        assert cli("dashboard", "--log-file", "/tmp/scheduler.log").code == 0
        assert _paths(wired).log_path == Path("/tmp/scheduler.log")

    def test_log_file_default(self, cli, wired) -> None:
        assert cli("dashboard").code == 0
        assert _paths(wired).log_path == Path("/var/log/briar/scheduler.log")

    def test_disk_path_flag_sets_disk_path(self, cli, wired) -> None:
        assert cli("dashboard", "--disk-path", "/mnt/data").code == 0
        assert _paths(wired).disk_path == Path("/mnt/data")

    def test_disk_path_default(self, cli, wired) -> None:
        assert cli("dashboard").code == 0
        assert _paths(wired).disk_path == Path("/")

    def test_repo_path_flag_sets_repo_path(self, cli, wired) -> None:
        assert cli("dashboard", "--repo-path", "/opt/checkout").code == 0
        assert _paths(wired).repo_path == Path("/opt/checkout")

    def test_repo_path_default(self, cli, wired) -> None:
        assert cli("dashboard").code == 0
        assert _paths(wired).repo_path == Path(".")


class TestOnceFlag:
    def test_once_renders_to_stdout_and_skips_serve(self, cli, wired) -> None:
        result = cli("dashboard", "--once")
        assert result.code == 0
        assert "DASHBOARD" in result.out
        wired.server.render_index.assert_called_once_with()
        wired.server.serve.assert_not_called()

    def test_without_once_serves_and_does_not_render_to_stdout(self, cli, wired) -> None:
        result = cli("dashboard")
        assert result.code == 0
        wired.server.serve.assert_called_once_with()
        wired.server.render_index.assert_not_called()
        assert "DASHBOARD" not in result.out
