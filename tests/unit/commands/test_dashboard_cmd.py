"""`briar dashboard` — boot the control-panel server (read-write by default).

The HTTP server, stores, and collector registry are mocked at the seam
``commands/dashboard.py`` imports them from. We assert the server is
constructed with the right bind/port + read_only flag, that the action router
is wired for writes (and skipped under --read-only), that collectors are wired
in, and that `--once` renders to stdout without ever calling `serve()`.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def wired(mocker):
    """Patch all dashboard collaborators and return handles to assert on."""
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


class TestServe:
    def test_default_bind_and_port(self, cli, wired) -> None:
        result = cli("dashboard")
        assert result.code == 0
        # Server bound to loopback:8080 by default, read-write (control panel on).
        assert wired.server_ctor.call_args.kwargs == {"host": "127.0.0.1", "port": 8080, "read_only": False}
        # Collectors were wired in, then the blocking serve loop started.
        wired.server.set_collectors.assert_called_once_with(["COLLECTOR"])
        wired.server.serve.assert_called_once_with()

    def test_custom_host_and_port_forwarded(self, cli, wired) -> None:
        result = cli("dashboard", "--host", "0.0.0.0", "--port", "9001")
        assert result.code == 0
        assert wired.server_ctor.call_args.kwargs == {"host": "0.0.0.0", "port": 9001, "read_only": False}

    def test_read_write_by_default_wires_action_router(self, cli, wired) -> None:
        cli("dashboard")
        wired.server.set_action_router.assert_called_once()

    def test_read_only_flag_disables_writes(self, cli, wired) -> None:
        result = cli("dashboard", "--read-only")
        assert result.code == 0
        assert wired.server_ctor.call_args.kwargs["read_only"] is True
        wired.server.set_action_router.assert_not_called()

    def test_collectors_built_from_paths_and_dash(self, cli, wired) -> None:
        cli("dashboard")
        # from_paths(paths, dash) — both positional; paths carries the wired stores.
        paths, dash = wired.from_paths.call_args.args
        assert paths.knowledge_store == "KNOWLEDGE_STORE"
        assert paths.journal_store == "JOURNAL_STORE"
        assert dash.started_at == 123.0

    def test_once_renders_and_does_not_serve(self, cli, wired) -> None:
        result = cli("dashboard", "--once")
        assert result.code == 0
        assert "DASHBOARD" in result.out
        wired.server.render_index.assert_called_once_with()
        # `--once` must short-circuit before the blocking serve loop.
        wired.server.serve.assert_not_called()

    def test_knowledge_store_defaults_to_file_without_db_url(self, cli, wired) -> None:
        cli("dashboard")
        # No BRIAR_DATABASE_URL → file store (env_sandbox strips it).
        assert wired.make_store.call_args.args[0] == "file"

    def test_knowledge_store_defaults_to_postgres_with_db_url(self, cli, wired) -> None:
        cli("dashboard", env={"BRIAR_DATABASE_URL": "postgresql://placeholder/db"})
        assert wired.make_store.call_args.args[0] == "postgres"

    def test_explicit_knowledge_store_overrides_default(self, cli, wired) -> None:
        cli("dashboard", "--knowledge-store", "file", env={"BRIAR_DATABASE_URL": "postgresql://placeholder/db"})
        # Explicit flag beats the DB-URL-implied postgres default.
        assert wired.make_store.call_args.args[0] == "file"

    def test_journal_store_failure_disables_journal_not_command(self, cli, wired) -> None:
        # A broken journal store must not crash the dashboard — it degrades.
        wired.make_journal.side_effect = RuntimeError("journal root missing")
        result = cli("dashboard")
        assert result.code == 0
        paths, _dash = wired.from_paths.call_args.args
        assert paths.journal_store is None


class TestArgs:
    def test_bad_port_usage_error(self, cli, wired) -> None:
        result = cli("dashboard", "--port", "not-an-int")
        assert result.code == 2  # argparse type=int rejects it

    def test_invalid_knowledge_store_choice(self, cli, wired) -> None:
        result = cli("dashboard", "--knowledge-store", "carrier-pigeon")
        assert result.code == 2
        assert "invalid choice" in result.err

    def test_invalid_journal_store_choice(self, cli, wired) -> None:
        result = cli("dashboard", "--journal-store", "carrier-pigeon")
        assert result.code == 2
        assert "invalid choice" in result.err
