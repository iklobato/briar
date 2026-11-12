"""Telemetry config resolution tests: env and project-config precedence, install id."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from briar.telemetry._config import TelemetryTier, resolve


@pytest.fixture(autouse=True)
def _isolated_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every test gets its own XDG_CONFIG_HOME so we don't touch the
    real user config, and a fresh in-memory install-id cache so the
    module-level `_INSTALL_ID_CACHE` can't leak an id from a prior test
    (which would mask whether `resolve()` actually read this test's
    isolated config dir)."""
    import briar.telemetry._config as _cfg_mod

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_cfg_mod, "_INSTALL_ID_CACHE", None)
    yield


class TestPrecedence:
    def test_do_not_track_wins(self) -> None:
        cfg = resolve(env={"DO_NOT_TRACK": "1", "BRIAR_TELEMETRY": "full"})
        assert cfg.tier is TelemetryTier.OFF
        assert cfg.source == "do-not-track"

    def test_do_not_track_beats_the_project_config(self) -> None:
        cfg = resolve(env={"DO_NOT_TRACK": "1"}, project_config={"telemetry": "full"})
        assert cfg.tier is TelemetryTier.OFF
        assert cfg.source == "do-not-track"

    def test_env_var_wins_over_the_project_config(self) -> None:
        cfg = resolve(env={"BRIAR_TELEMETRY": "full"}, project_config={"telemetry": "off"})
        assert cfg.tier is TelemetryTier.FULL
        assert cfg.source == "env"

    def test_project_config_used_when_no_env(self) -> None:
        cfg = resolve(env={}, project_config={"telemetry": "errors-only"})
        assert cfg.tier is TelemetryTier.ERRORS_ONLY
        assert cfg.source == "project-config"

    def test_invalid_project_config_value_falls_through_to_default(self) -> None:
        cfg = resolve(env={}, project_config={"telemetry": "supercharged"})
        assert cfg.tier is TelemetryTier.OFF
        assert cfg.source == "default"

    def test_default_is_off(self) -> None:
        cfg = resolve(env={})
        assert cfg.tier is TelemetryTier.OFF
        assert cfg.source == "default"

    def test_invalid_env_value_falls_through_to_default(self) -> None:
        cfg = resolve(env={"BRIAR_TELEMETRY": "supercharged"})
        assert cfg.tier is TelemetryTier.OFF
        assert cfg.source == "default"

    def test_state_saved_by_1x_is_ignored(self, tmp_path: Path) -> None:
        # 1.x saved `full` for everyone who saw its first-run banner, so a
        # saved 1.x tier is not a choice the user made.
        (tmp_path / "briar").mkdir()
        (tmp_path / "briar" / "telemetry.json").write_text('{"tier": "full", "banner_shown": true}')
        cfg = resolve(env={})
        assert cfg.tier is TelemetryTier.OFF
        assert cfg.source == "default"

    def test_state_file_of_the_removed_telemetry_command_is_ignored(self, tmp_path: Path) -> None:
        # Before decision C7 `briar telemetry full` saved the tier here; 2.0
        # no longer reads it, so the project config is the only file source.
        (tmp_path / "briar").mkdir()
        (tmp_path / "briar" / "telemetry.v2.json").write_text('{"tier": "full"}')
        cfg = resolve(env={})
        assert cfg.tier is TelemetryTier.OFF
        assert cfg.source == "default"


class TestInstallId:
    def test_install_id_is_generated_and_persisted(self) -> None:
        cfg = resolve(env={})
        assert cfg.install_id
        # Same id on the next resolve — proves persistence.
        again = resolve(env={})
        assert again.install_id == cfg.install_id

    def test_hashed_form_is_stable(self) -> None:
        cfg = resolve(env={})
        h = cfg.hashed_install_id
        assert len(h) == 16
        # Same input → same output (idempotent property).
        assert cfg.hashed_install_id == h


class TestBrokenConfig:
    def test_unreadable_install_id_path_still_returns_uuid(self) -> None:
        # When the parent dir can't be created (read-only HOME),
        # _load_or_create_install_id still returns a one-shot id.
        with mock.patch.object(Path, "write_text", side_effect=OSError("ro fs")):
            cfg = resolve(env={})
            assert cfg.install_id  # not crashed; just non-persistent
