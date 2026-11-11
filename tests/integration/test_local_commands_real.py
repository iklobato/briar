"""End-to-end integration: real briar commands driving real LOCAL backends.

No network, no mocked stores or servers. Each test runs the REAL command
through the `cli` fixture (which calls `briar.cli.main(...)`) against a
real file/config backend rooted in a tmp dir, then asserts the FINAL
observable behavior: exit code, parsed stdout, files written on disk
(read back), and config/store state changed.

The `cli` fixture invokes the full startup path (credential bootstrap,
telemetry install). `env_sandbox` (autouse in
tests/conftest.py) strips every credential-shaped env var, so:
  * No remote bootstrap configured -> no network.
  * provider-backed extractors report missing creds deterministically.
`BRIAR_SECRETS_FILE` is already redirected per-test by `env_sandbox`.

These cover the runbook executor that `briar serve` runs for each scheduled extract
(`runbook extract` left in spec 003 part 4; the envfile writer left with `auth`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from briar.errors import CliError
from briar.iac.runbook import extract_runbook, load_runbook_file

pytestmark = pytest.mark.integration


# ─────────────────────── shared env helper ─────────────────────────


def _local_env(tmp_path: Path) -> dict[str, str]:
    """Pin every on-disk location the CLI startup + commands touch into
    the test's tmp dir so nothing leaks to the developer's real config.

    - BRIAR_TELEMETRY=off keeps the telemetry installer from doing work
      (and from reading the real ~/.config); its install id goes under
      XDG_CONFIG_HOME, which we also pin.
    """
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "BRIAR_TELEMETRY": "off",
    }


# ════════════════════════════ runbook ═══════════════════════════════


class TestRunbookExtractReal:
    def _write_runbook(self, tmp_path: Path) -> Path:
        """A real runbook whose only extractor is provider-backed
        (pr-archaeology), read from the wire fake: the login check passes and
        the pulls list is a 404, so the executor records 'empty (no sections)'."""
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir(exist_ok=True)
        rb = tmp_path / "acme.yaml"
        rb.write_text(
            "version: 1\n"
            "companies:\n"
            "  zzcorp:\n"
            "    knowledge:\n"
            "      store: file\n"
            "      name: knowledge:zzcorp\n"
            f"      root: {knowledge}\n"
            "    extract:\n"
            "      - name: pr-archaeology\n"
            "        args: {pr_repo: [zzcorp/api]}\n"
        )
        return rb

    def test_extract_runs_real_executor_records_row(self, tmp_path: Path, github_at: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        rb = self._write_runbook(tmp_path)
        for name, value in {**_local_env(tmp_path), "BRIAR_ZZCORP_GITHUB_TOKEN": "PLACEHOLDER-not-a-secret"}.items():
            monkeypatch.setenv(name, value)

        rows = extract_runbook(load_runbook_file(rb))

        # The pulls route is a 404 on the wire fake -> no sections collected.
        assert [(row.company, row.task, row.status, row.output) for row in rows] == [("zzcorp", "extractors", "empty (no sections)", "knowledge:zzcorp")]

    def test_extract_task_filter_no_match_skips(self, tmp_path: Path) -> None:
        rb = self._write_runbook(tmp_path)

        # Filter matched nothing; with an explicit filter, no rows emitted.
        assert extract_runbook(load_runbook_file(rb), "nonexistent") == []

    def test_extract_missing_file_is_error(self, tmp_path: Path) -> None:
        with pytest.raises(CliError, match="not found"):
            load_runbook_file(tmp_path / "does-not-exist.yaml")
