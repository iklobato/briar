"""`briar telemetry` — status / preview / off / errors-only / full / reset.

These ops never touch the network; the set-tier ops rewrite a local
JSON state file. We point ``XDG_CONFIG_HOME`` at a per-test tmp dir
(the config-dir resolver honours it) and assert the FILE STATE actually
changes — not just that a function ran.
"""

from __future__ import annotations

import json

import pytest

from briar.telemetry import TelemetryTier


@pytest.fixture
def xdg_home(monkeypatch, tmp_path):
    """Redirect telemetry config + install-id under a tmp dir, and clear
    the process-global install-id cache so reads/writes hit our tmp."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    import briar.telemetry._config as cfg

    monkeypatch.setattr(cfg, "_INSTALL_ID_CACHE", None)
    return tmp_path / "briar"


def _state(xdg_home) -> dict:
    return json.loads((xdg_home / "telemetry.json").read_text())


# ─────────────────────────── set-tier ──────────────────────────────


class TestSetTier:
    def test_off_writes_off_tier_to_state_file(self, cli, xdg_home) -> None:
        assert not (xdg_home / "telemetry.json").exists()
        result = cli("telemetry", "off")
        assert result.code == 0
        # Side effect: the on-disk tier is now OFF.
        assert _state(xdg_home)["tier"] == TelemetryTier.OFF.value
        assert "off" in result.out

    def test_errors_only_writes_errors_only_tier(self, cli, xdg_home) -> None:
        result = cli("telemetry", "errors-only")
        assert result.code == 0
        assert _state(xdg_home)["tier"] == TelemetryTier.ERRORS_ONLY.value

    def test_full_writes_full_tier(self, cli, xdg_home) -> None:
        result = cli("telemetry", "full")
        assert result.code == 0
        assert _state(xdg_home)["tier"] == TelemetryTier.FULL.value

    def test_off_then_full_overwrites_tier(self, cli, xdg_home) -> None:
        cli("telemetry", "off")
        assert _state(xdg_home)["tier"] == TelemetryTier.OFF.value
        cli("telemetry", "full")
        # The later choice replaces the earlier one in the same file.
        assert _state(xdg_home)["tier"] == TelemetryTier.FULL.value

    def test_set_tier_marks_banner_shown(self, cli, xdg_home) -> None:
        # save_tier(banner_shown=True) means the first-run banner won't re-show.
        cli("telemetry", "off")
        assert _state(xdg_home)["banner_shown"] is True


# ─────────────────────────── status ────────────────────────────────


class TestStatus:
    def test_status_reflects_persisted_off_tier(self, cli, xdg_home) -> None:
        cli("telemetry", "off")
        result = cli("telemetry", "status", "--format", "json")
        assert result.code == 0
        payload = json.loads(result.out)
        assert payload["tier"] == "off"
        assert payload["enabled"] is False
        assert payload["source"] == "config-file"

    def test_status_env_override_wins(self, cli, xdg_home) -> None:
        # BRIAR_TELEMETRY env beats the persisted config-file tier.
        cli("telemetry", "full")
        result = cli("telemetry", "status", "--format", "json", env={"BRIAR_TELEMETRY": "off"})
        assert result.code == 0
        payload = json.loads(result.out)
        assert payload["tier"] == "off"
        assert payload["source"] == "env"

    def test_status_never_prints_raw_install_id(self, cli, xdg_home) -> None:
        # install_id file holds the raw UUID; status must only emit the hash.
        cli("telemetry", "reset")
        raw_id = (xdg_home / "install_id").read_text().strip()
        result = cli("telemetry", "status", "--format", "json")
        payload = json.loads(result.out)
        assert payload["install_id_hashed"] != raw_id
        assert raw_id not in result.out
        assert len(payload["install_id_hashed"]) == 16


# ─────────────────────────── preview ───────────────────────────────


class TestPreview:
    def test_preview_emits_json_event_not_sent(self, cli, xdg_home) -> None:
        result = cli("telemetry", "preview", "--for-command", "plan.run")
        assert result.code == 0
        event = json.loads(result.out)
        assert event["command"] == "plan.run"
        # A preview is explicitly marked so no consumer treats it as a real run.
        assert event["outcome"] == "preview"

    def test_preview_default_command_label(self, cli, xdg_home) -> None:
        result = cli("telemetry", "preview")
        event = json.loads(result.out)
        assert event["command"] == "(preview)"


# ─────────────────────────── reset ─────────────────────────────────


class TestReset:
    def test_reset_rotates_install_id_file(self, cli, xdg_home, monkeypatch) -> None:
        import briar.telemetry._config as cfg

        # Seed an existing id.
        (xdg_home).mkdir(parents=True, exist_ok=True)
        (xdg_home / "install_id").write_text("INSTALL-ID-PLACEHOLDER-old")
        monkeypatch.setattr(cfg, "_INSTALL_ID_CACHE", None)

        result = cli("telemetry", "reset")
        assert result.code == 0
        new_id = (xdg_home / "install_id").read_text().strip()
        # The persisted id actually changed.
        assert new_id != "INSTALL-ID-PLACEHOLDER-old"
        assert "rotated" in result.out or json.loads(result.out).get("rotated") is True


class TestDispatch:
    def test_missing_op_usage_error(self, cli) -> None:
        result = cli("telemetry")
        assert result.code == 2

    def test_unknown_op_usage_error(self, cli) -> None:
        result = cli("telemetry", "frobnicate")
        assert result.code == 2
        assert "invalid choice" in result.err
