"""2.0 removed the SSM, Secrets Manager and Vault credential stores.

A user who still points `BRIAR_DEFAULT_STORE` at one of them gets one clear
line instead of a run that silently lacks the keys that store used to load.
Help and `--version` keep working so the user can read how to fix it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

import pytest

REMOVED_STORES = ("ssm", "aws-secretsmanager", "vault")


@pytest.mark.parametrize("store", REMOVED_STORES)
def test_a_command_stops_with_an_error_naming_the_store_and_the_fix(cli: Callable[..., SimpleNamespace], store: str) -> None:
    run = cli("doctor", env={"BRIAR_DEFAULT_STORE": store})

    assert run.code == 1
    assert run.err == (
        f"error: credential store '{store}' was removed in briar 2.0; " "unset BRIAR_DEFAULT_STORE or move the keys to the env file (briar auth login)\n"
    )


@pytest.mark.parametrize("argv", [("--version",), ("--help",), ("doctor", "--help")])
def test_help_and_version_still_work(cli: Callable[..., SimpleNamespace], argv: tuple) -> None:
    run = cli(*argv, env={"BRIAR_DEFAULT_STORE": "vault"})

    assert run.code == 0
    assert "removed in briar 2.0" not in run.err


def test_envfile_store_is_not_blocked(cli: Callable[..., SimpleNamespace]) -> None:
    run = cli("doctor", env={"BRIAR_DEFAULT_STORE": "envfile"})

    assert "removed in briar 2.0" not in run.err
