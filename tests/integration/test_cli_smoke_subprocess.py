"""Tier-3 smoke: run the REAL installed entrypoint as a subprocess.

These don't import briar in-process; they spawn `[sys.executable, "-m",
"briar", ...]` so the *actual* console behavior is exercised end-to-end:
argument parsing, startup bootstrap, command dispatch, process exit code,
and stdout bytes — exactly what a user gets on a terminal.

Safety:
  * A clean, minimal env so the developer's real credentials / config are
    never read. `BRIAR_TELEMETRY=off` + `BRIAR_JOURNAL=off` keep startup
    side-effect-free; `BRIAR_SECRETS_FILE` + `XDG_CONFIG_HOME` point at a
    per-test tmp dir; credential-shaped vars are dropped so no bootstrap
    reaches the network.
  * Only read-only / stdout-only commands (version, --help, telemetry
    status, scaffold to stdout).
  * Every call has a hard timeout (<= 30s, well under the suite's per-test
    cap).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


_TIMEOUT = 30
_CRED_PREFIXES = (
    "BRIAR_",
    "GITHUB_",
    "JIRA_",
    "AWS_",
    "ANTHROPIC_",
    "OPENAI_",
    "GEMINI_",
    "BITBUCKET_",
    "LINEAR_",
    "FIREFLIES_",
    "INFISICAL_",
    "VAULT_",
    "SLACK_",
    "TELEGRAM_",
    "PAGERDUTY_",
    "SMTP_",
    "EMAIL_",
)


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Spawn `python -m briar <args>` in a sandboxed env rooted at tmp."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(_CRED_PREFIXES) and not k.startswith("DO_NOT_TRACK")}
    env.update(
        {
            "BRIAR_TELEMETRY": "off",
            "BRIAR_JOURNAL": "off",
            "BRIAR_SECRETS_FILE": str(tmp_path / "secrets.env"),  # nonexistent -> bootstrap no-op
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "briar", *args],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=env,
        cwd=str(tmp_path),
    )


class TestSmokeVersionAndHelp:
    def test_version_exit_0_prints_version(self, tmp_path) -> None:
        result = _run(tmp_path, "version")
        assert result.returncode == 0, result.stderr
        # Output shape: `briar-cli <version>`; version is non-empty.
        assert result.stdout.startswith("briar-cli ")
        printed = result.stdout.strip().split(" ", 1)[1]
        assert printed  # a real version string, not blank

    def test_top_level_help_exit_0_lists_commands(self, tmp_path) -> None:
        result = _run(tmp_path, "--help")
        assert result.returncode == 0, result.stderr
        # The help text enumerates the command surface.
        for command in ("scaffold", "context", "telemetry", "journal", "runbook", "secrets"):
            assert command in result.stdout

    @pytest.mark.parametrize(
        "command",
        ["scaffold", "context", "telemetry", "journal", "runbook", "secrets", "dashboard", "version"],
        ids=lambda c: f"help-{c}",
    )
    def test_per_command_help_exit_0(self, tmp_path, command) -> None:
        result = _run(tmp_path, command, "--help")
        assert result.returncode == 0, result.stderr
        # argparse prints a usage line naming the command.
        assert command in result.stdout
        assert "usage:" in result.stdout.lower()


class TestSmokeTelemetryStatus:
    def test_telemetry_status_exit_0(self, tmp_path) -> None:
        result = _run(tmp_path, "--format", "json", "telemetry", "status")
        assert result.returncode == 0, result.stderr
        status = json.loads(result.stdout)
        # BRIAR_TELEMETRY=off in the env -> resolved tier is off, source env.
        assert status["tier"] == "off"
        assert status["source"] == "env"
        assert status["enabled"] is False


class TestSmokeScaffold:
    def test_scaffold_implementation_to_stdout_emits_valid_json(self, tmp_path) -> None:
        result = _run(
            tmp_path,
            "scaffold",
            "implementation",
            "--prefix",
            "smoke",
            "--source",
            "github",
            "--owner",
            "alice",
            "--repo",
            "widgets",
            "--out",
            "-",
        )
        assert result.returncode == 0, result.stderr
        bundle = json.loads(result.stdout)
        # The flags flowed through the real composer.
        assert bundle["version"] == 1
        assert bundle["agents"][0]["key"] == "smoke-engineer"
        assert bundle["sources"][0]["config"]["owner"] == "alice"
        assert bundle["sources"][0]["config"]["repo"] == "alice/widgets"

    def test_scaffold_missing_prefix_exit_2(self, tmp_path) -> None:
        result = _run(
            tmp_path,
            "scaffold",
            "implementation",
            "--source",
            "github",
            "--owner",
            "o",
            "--repo",
            "r",
        )
        # --prefix is required -> argparse usage error.
        assert result.returncode == 2
        assert "prefix" in result.stderr

    def test_unknown_command_exit_2(self, tmp_path) -> None:
        result = _run(tmp_path, "frobnicate")
        assert result.returncode == 2
        assert "invalid choice" in result.stderr
