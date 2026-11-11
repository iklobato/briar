"""Exit codes and the help I/O contract, as 1.x has them (spec 002, T004).

0 ok, 1 the CLI's own error (`error: <message>` on stderr), 2 a usage error
or an unexpected crash, 130 Ctrl-C. Usage messages are checked only for the
word `usage:` and the offending token: Typer words them differently, the
exit code and the token are the contract.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, List, Tuple

import pytest

from briar.commands.doctor import CommandDoctor
from briar.errors import CliError

from .conftest import Tripwire

OK, CLI_ERROR, USAGE_ERROR, INTERRUPTED = 0, 1, 2, 130


class RaisingRun:
    """Stands in for a command's `run()` and raises the given exception."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def raise_error(self, args: argparse.Namespace) -> int:
        raise self._error


class UsageErrors:
    """Bad command lines and the token stderr must name (empty: none to name)."""

    CASES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
        ((), ""),
        (("nope",), "nope"),
        (("runbook",), ""),
        (("doctor", "--nope"), "--nope"),
        (("extract",), "--company"),
        (("--format", "xml", "doctor"), "xml"),
        # Commands briar 2.0 removed get the usual unknown-command error (decision C22).
        (("agent", "implement"), "No such command 'agent'"),
        (("plan", "list"), "No such command 'plan'"),
        (("context", "list"), "No such command 'context'"),
        (("secrets", "doctor"), "No such command 'secrets'"),
        (("journal", "list"), "No such command 'journal'"),
        (("telemetry", "status"), "No such command 'telemetry'"),
        (("version",), "No such command 'version'"),
        (("config", "show"), "No such command 'config'"),
        (("runbook", "sweep", "runbooks/"), "No such command 'sweep'"),
        (("auth", "list"), "No such command 'list'"),
        (("auth", "logout", "github-pat"), "No such command 'logout'"),
        (("auth", "refresh", "github-pat"), "No such command 'refresh'"),
    )

    def ids(self) -> List[str]:
        return [" ".join(argv) or "no-command" for argv, _ in self.CASES]


class TestExitCodes:
    def test_success_is_zero(self, cli: Callable[..., SimpleNamespace]) -> None:
        assert cli("doctor").code == OK

    def test_cli_error_is_one_with_the_error_line(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(CommandDoctor, "run", RaisingRun(CliError("the runbook is missing")).raise_error)

        run = cli("doctor")

        assert run.code == CLI_ERROR
        assert "error: the runbook is missing" in run.err.splitlines()

    def test_global_flag_without_value_is_one(self, cli: Callable[..., SimpleNamespace]) -> None:
        run = cli("doctor", "--format")

        assert (run.code, run.err) == (CLI_ERROR, "error: --format requires a value\n")

    @pytest.mark.parametrize(("argv", "token"), UsageErrors.CASES, ids=UsageErrors().ids())
    def test_usage_error_is_two(self, cli: Callable[..., SimpleNamespace], argv: Tuple[str, ...], token: str) -> None:
        run = cli(*argv)

        assert run.code == USAGE_ERROR
        assert "usage:" in run.err.lower()
        assert token in run.err

    def test_unexpected_crash_is_two(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(CommandDoctor, "run", RaisingRun(RuntimeError("bug")).raise_error)

        assert cli("doctor").code == USAGE_ERROR

    def test_ctrl_c_is_130(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(CommandDoctor, "run", RaisingRun(KeyboardInterrupt()).raise_error)

        run = cli("doctor")

        assert run.code == INTERRUPTED
        assert "interrupted" in run.err.splitlines()


class TestRemovedRunbookBlocks:
    """Only the 1.x agent read these blocks (spec 006, FR-006a): a runbook that still has one stops."""

    @pytest.mark.parametrize("block", ["mcp", "git_identity", "messages"])
    def test_runbook_with_a_removed_block_is_one_naming_it(self, cli: Callable[..., SimpleNamespace], tmp_path: Path, block: str) -> None:
        runbook = tmp_path / "runbook.yaml"
        runbook.write_text(f"version: 1\ncompanies:\n  acme:\n    {block}: {{}}\n")

        run = cli("runbook", "extract", str(runbook))

        assert run.code == CLI_ERROR
        assert f"`{block}` was removed in briar 2.0" in run.err

    def test_runbook_with_the_postgres_store_is_one_naming_it(self, cli: Callable[..., SimpleNamespace], tmp_path: Path) -> None:
        runbook = tmp_path / "runbook.yaml"
        runbook.write_text("version: 1\ncompanies:\n  acme:\n    knowledge:\n      store: postgres\n")

        run = cli("runbook", "extract", str(runbook))

        assert run.code == CLI_ERROR
        assert "`store: postgres` was removed in briar 2.0" in run.err


class TestHelpDoesNoIo:
    @pytest.mark.parametrize("argv", [("--help",), ("-h",), ("runbook", "--help"), ("auth", "login", "-h"), ("runbook", "extract", "--help")], ids=" ".join)
    def test_help_exits_zero_without_startup_io(self, cli: Callable[..., SimpleNamespace], tripwire: Tripwire, argv: Tuple[str, ...]) -> None:
        run = cli(*argv)

        assert (run.code, tripwire.calls) == (OK, [])
        assert "usage:" in run.out.lower()

    def test_no_command_exits_two_without_startup_io(self, cli: Callable[..., SimpleNamespace], tripwire: Tripwire) -> None:
        run = cli()

        assert (run.code, tripwire.calls) == (USAGE_ERROR, [])
