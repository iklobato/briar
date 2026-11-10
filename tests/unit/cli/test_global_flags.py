"""The global flags work before and after the subcommand (spec 002, T003).

1.x pulls `--format`, `-v`/`--verbose` and `--version`/`-V` out of argv
wherever they appear (`cli._extract_global_flags`). Typer only sees options
after the command they belong to, so spec 002 keeps a pre-parse; these
tests pin the behaviour it must keep.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable, List
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from briar import __version__

from .conftest import Tripwire

SHOW = ("config", "show")
FORMATS = ("json", "table")
VERSION_LINE = f"briar-cli {__version__}\n"
STARTUP_STEPS = ("credential bootstrap", "project config", "telemetry")


class Placements:
    """The same global flag written before the command, after it, and as `--flag=value`."""

    def format_argvs(self, fmt: str) -> List[List[str]]:
        return [["--format", fmt, *SHOW], [*SHOW, "--format", fmt], [f"--format={fmt}", *SHOW], [*SHOW, f"--format={fmt}"]]

    def bool_argvs(self, flag: str) -> List[List[str]]:
        return [[flag, *SHOW], [*SHOW, flag], [SHOW[0], flag, SHOW[1]]]


@pytest.fixture
def logging_spy(cli: Callable[..., SimpleNamespace], mocker: MockerFixture) -> MagicMock:
    """Runs after `cli`, which replaces `configure_logging` with a no-op. Logging is not
    really configured: `basicConfig(force=True)` would drop pytest's capture handlers."""
    return mocker.patch("briar.cli.configure_logging")


class TestFormat:
    @pytest.mark.parametrize("fmt", FORMATS)
    def test_every_placement_prints_the_same_bytes(self, cli: Callable[..., SimpleNamespace], fmt: str) -> None:
        outputs = {tuple(argv): cli(*argv) for argv in Placements().format_argvs(fmt)}

        assert {run.code for run in outputs.values()} == {0}
        assert len({run.out for run in outputs.values()}) == 1, outputs

    def test_format_changes_the_output(self, cli: Callable[..., SimpleNamespace]) -> None:
        """Guards the test above: it would also pass if `--format` were ignored."""
        assert cli("--format", "json", *SHOW).out != cli("--format", "table", *SHOW).out


class TestVerbose:
    @pytest.mark.parametrize("flag", ["-v", "--verbose"])
    def test_every_placement_turns_on_debug_logging(self, cli: Callable[..., SimpleNamespace], logging_spy: MagicMock, flag: str) -> None:
        runs = [cli(*argv) for argv in Placements().bool_argvs(flag)]

        assert [run.code for run in runs] == [0, 0, 0]
        assert [call.kwargs["verbose"] for call in logging_spy.call_args_list] == [True, True, True]

    def test_without_the_flag_logging_stays_quiet(self, cli: Callable[..., SimpleNamespace], logging_spy: MagicMock) -> None:
        cli(*SHOW)

        assert [call.kwargs["verbose"] for call in logging_spy.call_args_list] == [False]


class TestVersion:
    @pytest.mark.parametrize("flag", ["--version", "-V"])
    def test_every_placement_prints_the_version(self, cli: Callable[..., SimpleNamespace], tripwire: Tripwire, flag: str) -> None:
        runs = [cli(*argv) for argv in Placements().bool_argvs(flag)]

        assert [(run.code, run.out, run.err) for run in runs] == [(0, VERSION_LINE, "")] * len(runs)

    def test_version_does_no_startup_io(self, cli: Callable[..., SimpleNamespace], tripwire: Tripwire) -> None:
        cli("--version")

        assert tripwire.calls == []

    def test_tripwire_sees_a_real_command(self, cli: Callable[..., SimpleNamespace], tripwire: Tripwire) -> None:
        """Guards the test above: it would also pass if the tripwire were never wired."""
        cli(*SHOW)

        assert tuple(tripwire.calls[: len(STARTUP_STEPS)]) == STARTUP_STEPS
