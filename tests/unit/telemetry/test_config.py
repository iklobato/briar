"""Telemetry config resolution tests — env precedence + persistence."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from briar.telemetry._config import TelemetryTier, reset_install_id, resolve, save_tier, state_path


@pytest.fixture(autouse=True)
def _isolated_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every test gets its own XDG_CONFIG_HOME so we don't touch the
    real user config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    yield


class TestPrecedence:
    def test_do_not_track_wins(self) -> None:
        cfg = resolve(env={"DO_NOT_TRACK": "1", "BRIAR_TELEMETRY": "full"})
        assert cfg.tier is TelemetryTier.OFF
        assert cfg.source == "do-not-track"

    def test_env_var_wins_over_config_file(self) -> None:
        save_tier(TelemetryTier.OFF)
        cfg = resolve(env={"BRIAR_TELEMETRY": "full"})
        assert cfg.tier is TelemetryTier.FULL
        assert cfg.source == "env"

    def test_config_file_used_when_no_env(self) -> None:
        save_tier(TelemetryTier.ERRORS_ONLY)
        cfg = resolve(env={})
        assert cfg.tier is TelemetryTier.ERRORS_ONLY
        assert cfg.source == "config-file"

    def test_default_full(self) -> None:
        cfg = resolve(env={})
        assert cfg.tier is TelemetryTier.FULL
        assert cfg.source == "default"

    def test_invalid_env_value_falls_through_to_default(self) -> None:
        cfg = resolve(env={"BRIAR_TELEMETRY": "supercharged"})
        assert cfg.tier is TelemetryTier.FULL
        assert cfg.source == "default"


class TestInstallId:
    def test_install_id_is_generated_and_persisted(self) -> None:
        cfg = resolve(env={})
        assert cfg.install_id
        # Same id on the next resolve — proves persistence.
        again = resolve(env={})
        assert again.install_id == cfg.install_id

    def test_reset_rotates_id(self) -> None:
        first = resolve(env={}).install_id
        new = reset_install_id()
        assert new != first
        assert resolve(env={}).install_id == new

    def test_hashed_form_is_stable(self) -> None:
        cfg = resolve(env={})
        h = cfg.hashed_install_id
        assert len(h) == 16
        # Same input → same output (idempotent property).
        assert cfg.hashed_install_id == h


class TestSaveTier:
    def test_save_writes_state_file(self) -> None:
        save_tier(TelemetryTier.ERRORS_ONLY)
        assert state_path().exists()
        # Round-trip via resolve.
        cfg = resolve(env={})
        assert cfg.tier is TelemetryTier.ERRORS_ONLY
        assert cfg.banner_shown is True


class TestBrokenConfig:
    def test_corrupted_state_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text("not json {")
        cfg = resolve(env={})
        assert cfg.tier is TelemetryTier.FULL
        assert cfg.source == "default"

    def test_unreadable_install_id_path_still_returns_uuid(self) -> None:
        # When the parent dir can't be created (read-only HOME),
        # _load_or_create_install_id still returns a one-shot id.
        with mock.patch.object(Path, "write_text", side_effect=OSError("ro fs")):
            cfg = resolve(env={})
            assert cfg.install_id  # not crashed; just non-persistent
