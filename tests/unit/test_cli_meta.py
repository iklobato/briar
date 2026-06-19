"""Top-level metadata flags: --version is clean and side-effect-free."""

from __future__ import annotations

import logging

import briar.cli as cli_module
from briar import __version__


def test_version_flag_prints_and_exits_zero(capsys, monkeypatch):
    # If --version did real work it would call configure_logging / bootstrap;
    # make those explode so the test fails loudly on any side effect.
    monkeypatch.setattr(cli_module, "configure_logging", _boom)
    code = cli_module.main(["--version"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.strip() == f"briar-cli {__version__}"


def test_version_short_flag(capsys, monkeypatch):
    monkeypatch.setattr(cli_module, "configure_logging", _boom)
    assert cli_module.main(["-V"]) == 0
    assert __version__ in capsys.readouterr().out


def _boom(*a, **k):
    raise AssertionError("--version must not configure logging or do any I/O")


def test_quiet_by_default_then_env_and_verbose(monkeypatch):
    from briar.logging import _resolve_level

    monkeypatch.delenv("BRIAR_VERBOSE", raising=False)
    monkeypatch.delenv("BRIAR_LOG_LEVEL", raising=False)
    assert _resolve_level(False) == logging.WARNING  # quiet default
    assert _resolve_level(True) == logging.DEBUG  # --verbose

    monkeypatch.setenv("BRIAR_LOG_LEVEL", "info")
    assert _resolve_level(False) == logging.INFO  # explicit env override

    monkeypatch.setenv("BRIAR_VERBOSE", "1")
    assert _resolve_level(False) == logging.DEBUG  # verbose beats BRIAR_LOG_LEVEL


def test_daemon_logging_raises_to_info(monkeypatch):
    from briar import logging as briar_logging

    briar_logging.configure(verbose=False)  # WARNING default
    briar_logger = logging.getLogger("briar")
    assert briar_logger.getEffectiveLevel() == logging.WARNING
    briar_logging.daemon_logging()
    assert logging.getLogger("briar").getEffectiveLevel() == logging.INFO
