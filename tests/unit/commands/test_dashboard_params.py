"""`briar dashboard` — parametric per-flag EFFECT assertions.

Companion to ``test_dashboard_cmd.py`` (which covers bind/port,
knowledge-store default resolution, ``--once`` vs serve, and the
choice-validation exits). This file pins the REMAINING flags from
``/tmp/cli_manifest/dashboard.md`` to an observable effect — every
path flag must land on the ``DashboardPaths`` struct the collector
registry is built from, and the two file-root flags must reach the
store factories.

All collaborators (the HTTP server, both store factories, and the
collector-registry constructor) are mocked at the
``commands/dashboard.py`` import seam. ``from_paths(paths, dash)``
receives the real ``DashboardPaths`` instance, so a flag the command
fails to thread through makes the assertion FAIL.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def wired(mocker):
    """Patch every dashboard collaborator; return handles to assert on.

    Mirrors the fixture in ``test_dashboard_cmd.py`` (kept local so the
    two files stay independent — extend, don't share)."""
    fake_server = mocker.MagicMock()
    fake_server.started_at = 123.0
    fake_server.render_index.return_value = "<html>DASHBOARD</html>"
    server_ctor = mocker.patch("briar.commands.dashboard.DashboardServer", return_value=fake_server)

    make_store = mocker.patch("briar.commands.dashboard.make_store", return_value="KNOWLEDGE_STORE")
    make_journal = mocker.patch("briar.commands.dashboard.make_journal_store", return_value="JOURNAL_STORE")
    from_paths = mocker.patch("briar.commands.dashboard.CollectorRegistry.from_paths", return_value=["COLLECTOR"])

    return mocker.MagicMock(
        server=fake_server,
        server_ctor=server_ctor,
        make_store=make_store,
        make_journal=make_journal,
        from_paths=from_paths,
    )


def _paths(wired):
    """The DashboardPaths instance passed to from_paths(paths, dash)."""
    paths, _dash = wired.from_paths.call_args.args
    return paths


# ─── --host / --port (defaults; non-default covered in test_dashboard_cmd) ─


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


# ─── path flags → DashboardPaths ──────────────────────────────────────


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

    def test_secrets_file_flag_sets_secrets_path(self, cli, wired) -> None:
        assert cli("dashboard", "--secrets-file", "/run/secrets.env").code == 0
        assert _paths(wired).secrets_path == Path("/run/secrets.env")

    def test_secrets_file_default(self, cli, wired) -> None:
        assert cli("dashboard").code == 0
        assert _paths(wired).secrets_path == Path("/etc/briar/secrets.env")


# ─── --du-path (repeatable; default derives from other paths) ─────────


class TestDuPath:
    def test_du_path_repeatable_all_collected(self, cli, wired) -> None:
        assert cli("dashboard", "--du-path", "/a", "--du-path", "/b").code == 0
        assert _paths(wired).du_paths == [Path("/a"), Path("/b")]

    def test_du_path_default_derives_from_repo_knowledge_and_logdir(self, cli, wired) -> None:
        # Empty --du-path → [repo_path, knowledge, /var/log/briar].
        assert cli("dashboard", "--repo-path", "/opt/co", "--knowledge", "/data/kb").code == 0
        assert _paths(wired).du_paths == [Path("/opt/co"), Path("/data/kb"), Path("/var/log/briar")]


# ─── --knowledge / --knowledge-store → make_store(file_root=...) ──────


class TestKnowledgeRoot:
    def test_knowledge_root_reaches_make_store(self, cli, wired) -> None:
        assert cli("dashboard", "--knowledge-store", "file", "--knowledge", "/data/kb").code == 0
        assert wired.make_store.call_args.kwargs["file_root"] == Path("/data/kb")

    def test_knowledge_root_default(self, cli, wired) -> None:
        assert cli("dashboard", "--knowledge-store", "file").code == 0
        assert wired.make_store.call_args.kwargs["file_root"] == Path("./knowledge")

    @pytest.mark.parametrize("store", ["", "file", "postgres"], ids=["empty", "file", "postgres"])
    def test_knowledge_store_choices_accepted(self, cli, wired, store) -> None:
        argv = ["dashboard"]
        if store:
            argv += ["--knowledge-store", store]
        result = cli(*argv)
        assert result.code == 0
        # empty "" → resolves to file (no BRIAR_DATABASE_URL in sandbox)
        expected = store or "file"
        assert wired.make_store.call_args.args[0] == expected

    def test_knowledge_store_invalid_choice_exits_2(self, cli, wired) -> None:
        result = cli("dashboard", "--knowledge-store", "redis")
        assert result.code == 2
        assert "invalid choice" in result.err


# ─── --journal-store / --journal-root → make_journal_store ────────────


class TestJournalFlags:
    def test_journal_store_choice_reaches_factory(self, cli, wired) -> None:
        assert cli("dashboard", "--journal-store", "file").code == 0
        assert wired.make_journal.call_args.args[0] == "file"

    def test_journal_store_default_is_file(self, cli, wired) -> None:
        assert cli("dashboard").code == 0
        assert wired.make_journal.call_args.args[0] == "file"

    def test_journal_root_reaches_factory(self, cli, wired) -> None:
        assert cli("dashboard", "--journal-root", "/data/journal").code == 0
        assert wired.make_journal.call_args.kwargs["file_root"] == Path("/data/journal")

    def test_journal_root_default(self, cli, wired) -> None:
        assert cli("dashboard").code == 0
        assert wired.make_journal.call_args.kwargs["file_root"] == Path("./journal")

    def test_journal_store_invalid_choice_exits_2(self, cli, wired) -> None:
        result = cli("dashboard", "--journal-store", "sqlite")
        assert result.code == 2
        assert "invalid choice" in result.err

    def test_journal_store_wired_into_paths(self, cli, wired) -> None:
        assert cli("dashboard").code == 0
        assert _paths(wired).journal_store == "JOURNAL_STORE"


# ─── --once (store_true): render-vs-serve ─────────────────────────────


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
