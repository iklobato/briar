"""Top-level CLI dispatcher.

Catches: flag order coupling, missing exit-code mappings,
journal/bootstrap failures escaping to user."""

from __future__ import annotations

import pytest

from briar.errors import CliError


class TestVersion:
    def test_version_command_exit_0(self, cli) -> None:
        result = cli("version")
        assert result.code == 0
        assert "briar-cli" in result.out


class TestGlobalFlags:
    def test_format_flag_before_command(self, cli) -> None:
        result = cli("--format", "json", "version")
        assert result.code == 0

    def test_format_flag_after_command(self, cli) -> None:
        result = cli("version", "--format", "json")
        assert result.code == 0

    def test_format_equals_form(self, cli) -> None:
        result = cli("--format=json", "version")
        assert result.code == 0

    def test_format_missing_value_exit_1(self, cli) -> None:
        # `--format` is the last token; no value follows.
        result = cli("--format")
        assert result.code == 1
        assert "--format" in result.err
        assert "requires a value" in result.err

    def test_verbose_short_flag(self, cli) -> None:
        result = cli("-v", "version")
        assert result.code == 0

    def test_verbose_long_flag(self, cli) -> None:
        result = cli("--verbose", "version")
        assert result.code == 0

    def test_briar_verbose_env_sets_debug(self, cli) -> None:
        result = cli("version", env={"BRIAR_VERBOSE": "1"})
        assert result.code == 0


class TestErrorHandling:
    def test_unknown_command_exits_2(self, cli) -> None:
        # argparse rejects unknown subcommand with exit 2.
        result = cli("does-not-exist")
        assert result.code == 2

    def test_cli_error_in_command_prints_to_stderr_exit_1(self, cli, mocker) -> None:
        # Inject a CliError-raising command via the registry.
        import briar.cli as cli_mod

        fake_cmd = mocker.MagicMock()
        fake_cmd.run.side_effect = CliError("custom error")
        fake_cmd.help = "fake"

        def add_arguments(parser):
            return None

        fake_cmd.add_arguments = add_arguments
        mocker.patch.object(
            cli_mod,
            "build_registry",
            return_value={"version": fake_cmd},
        )
        result = cli("version")
        assert result.code == 1
        assert "custom error" in result.err

    def test_keyboard_interrupt_exits_130(self, cli, mocker) -> None:
        import briar.cli as cli_mod

        fake_cmd = mocker.MagicMock()
        fake_cmd.run.side_effect = KeyboardInterrupt()
        fake_cmd.help = "fake"
        fake_cmd.add_arguments = lambda p: None
        mocker.patch.object(cli_mod, "build_registry", return_value={"version": fake_cmd})
        result = cli("version")
        assert result.code == 130

    def test_unhandled_exception_exits_2(self, cli, mocker, caplog_briar) -> None:
        import briar.cli as cli_mod

        fake_cmd = mocker.MagicMock()
        fake_cmd.run.side_effect = ValueError("surprise")
        fake_cmd.help = "fake"
        fake_cmd.add_arguments = lambda p: None
        mocker.patch.object(cli_mod, "build_registry", return_value={"version": fake_cmd})
        result = cli("version")
        assert result.code == 2
        # The traceback should be in the logs (log.exception).
        assert any("crashed unexpectedly" in r.message for r in caplog_briar.records)


class TestJournalInstall:
    @pytest.mark.parametrize("value", ["off", "0", "no", "OFF"])
    def test_briar_journal_off_skips_install(self, cli, value, mocker) -> None:
        # `set_active_journal` must not be called when journal is off.
        set_active = mocker.patch("briar.journal._journal.set_active_journal")
        result = cli("version", env={"BRIAR_JOURNAL": value})
        assert result.code == 0
        set_active.assert_not_called()

    def test_journal_install_failure_logged_does_not_crash(self, cli, mocker, caplog_briar) -> None:
        # Force the journal-store factory to blow up.
        mocker.patch("briar.journal.make_journal_store", side_effect=RuntimeError("store broken"))
        result = cli("version")
        assert result.code == 0
        # The exception should be logged but not propagated.
        assert any("journal: install failed" in r.message for r in caplog_briar.records)


class TestBootstrap:
    def test_bootstrap_failure_logs_warning_continues(self, cli, mocker, caplog_briar) -> None:
        from briar.credentials._bootstrap import HydrateResult

        mocker.patch(
            "briar.credentials._bootstraps.auto_bootstrap",
            return_value=HydrateResult(backend="fake", written=0, skipped=0, error="fail"),
        )
        result = cli("version")
        assert result.code == 0
        assert any("credential-bootstrap" in r.message and "failed" in r.message for r in caplog_briar.records)
