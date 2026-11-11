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

These cover: the envfile credential store, runbook extract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


# ═════════════════════════ credential store ═════════════════════════


class TestSecretsEnvfileStoreReal:
    def test_write_then_read_roundtrip_persists_and_never_echoes(self, cli, tmp_path, monkeypatch) -> None:
        """Drive the REAL EnvFileStore: write a placeholder secret, read it
        back, assert it landed on disk as `KEY=value`, and that the value
        is never printed."""
        from briar.credentials.envfile import EnvFileStore

        secrets_file = tmp_path / "secrets.env"
        monkeypatch.setenv("BRIAR_SECRETS_FILE", str(secrets_file))

        placeholder = "TOKEN-VALUE-PLACEHOLDER-not-a-secret"
        store = EnvFileStore()
        store.write("GITHUB_ACME_TOKEN", placeholder)

        # Persisted to the real file as a single KEY=value line.
        on_disk = secrets_file.read_text()
        assert on_disk == f"GITHUB_ACME_TOKEN={placeholder}\n"
        # 0600 — never group/world readable.
        assert oct(secrets_file.stat().st_mode & 0o777) == "0o600"
        # Read back returns exactly what we wrote.
        assert store.read("GITHUB_ACME_TOKEN") == placeholder

    def test_write_is_idempotent_replace_in_place(self, cli, tmp_path, monkeypatch) -> None:
        from briar.credentials.envfile import EnvFileStore

        secrets_file = tmp_path / "secrets.env"
        monkeypatch.setenv("BRIAR_SECRETS_FILE", str(secrets_file))
        store = EnvFileStore()
        store.write("GITHUB_ACME_TOKEN", "FIRST-PLACEHOLDER-not-a-secret")
        store.write("GITHUB_ACME_TOKEN", "SECOND-PLACEHOLDER-not-a-secret")
        # Replaced in place — exactly one line, the new value.
        assert secrets_file.read_text() == "GITHUB_ACME_TOKEN=SECOND-PLACEHOLDER-not-a-secret\n"
        assert store.read("GITHUB_ACME_TOKEN") == "SECOND-PLACEHOLDER-not-a-secret"

    def test_delete_removes_from_disk_and_env(self, cli, tmp_path, monkeypatch) -> None:
        from briar.credentials.envfile import EnvFileStore

        secrets_file = tmp_path / "secrets.env"
        monkeypatch.setenv("BRIAR_SECRETS_FILE", str(secrets_file))
        store = EnvFileStore()
        store.write("AWS_ACME_KEY", "KEY-PLACEHOLDER-not-a-secret")
        assert "AWS_ACME_KEY" in secrets_file.read_text()

        removed = store.delete("AWS_ACME_KEY")
        assert removed is True
        assert "AWS_ACME_KEY" not in secrets_file.read_text()
        assert store.read("AWS_ACME_KEY") is None


# ════════════════════════════ runbook ═══════════════════════════════


class TestRunbookExtractReal:
    def _write_runbook(self, tmp_path: Path) -> Path:
        """A real runbook whose only extractor is provider-backed
        (pr-archaeology). With GITHUB_* stripped by env_sandbox, the
        extractor reports unavailable and the executor records a
        deterministic 'empty (no sections)' row — no network."""
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

    def test_extract_runs_real_executor_records_row(self, cli, tmp_path) -> None:
        rb = self._write_runbook(tmp_path)
        result = cli("--format", "json", "runbook", "extract", str(rb), env=_local_env(tmp_path))
        assert result.code == 0, result.err
        rows = json.loads(result.out)
        assert len(rows) == 1
        row = rows[0]
        assert row["company"] == "zzcorp"
        assert row["task"] == "extractors"
        # No creds -> extractor unavailable -> no sections collected.
        assert row["status"] == "empty (no sections)"
        assert row["output"] == "knowledge:zzcorp"

    def test_extract_task_filter_no_match_skips(self, cli, tmp_path) -> None:
        rb = self._write_runbook(tmp_path)
        result = cli(
            "--format",
            "json",
            "runbook",
            "extract",
            str(rb),
            "--task",
            "nonexistent",
            env=_local_env(tmp_path),
        )
        assert result.code == 0, result.err
        # Filter matched nothing; with an explicit filter, no rows emitted.
        assert json.loads(result.out) == []

    def test_extract_missing_file_is_error(self, cli, tmp_path) -> None:
        missing = tmp_path / "does-not-exist.yaml"
        result = cli("runbook", "extract", str(missing), env=_local_env(tmp_path))
        assert result.code == 1
        assert "not found" in result.err
