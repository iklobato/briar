"""Parametric flag/action-effect tests for `briar telemetry`.

Companion to ``test_telemetry_cmd.py``. ``/tmp/cli_manifest/telemetry.md`` lists
six action subcommands; only ``preview`` carries a flag (``--for-command``).
This file effect-asserts:

  * the ONE flag — ``--for-command`` — both its override and its default
    ("(preview)") are reflected in the emitted event's ``command`` field.
  * each flag-less action subcommand drives its documented side effect /
    output: off / errors-only / full write the matching tier to the on-disk
    state file; status prints the persisted tier; reset rotates the install_id;
    preview emits a not-sent event.

State is redirected under a tmp ``XDG_CONFIG_HOME`` so file mutations are
asserted directly. No network (these ops never touch it anyway).
"""

from __future__ import annotations

import json

import pytest

from briar.telemetry import TelemetryTier


@pytest.fixture
def xdg_home(monkeypatch, tmp_path):
    """Redirect telemetry config + install-id under a tmp dir, and clear the
    process-global install-id cache so reads/writes hit our tmp."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    import briar.telemetry._config as cfg

    monkeypatch.setattr(cfg, "_INSTALL_ID_CACHE", None)
    return tmp_path / "briar"


def _state(xdg_home) -> dict:
    return json.loads((xdg_home / "telemetry.json").read_text())


# ─── set-tier action subcommands (parametrized) ─────────────────────────


class TestSetTierActions:
    @pytest.mark.parametrize(
        "action, tier",
        [
            ("off", TelemetryTier.OFF),
            ("errors-only", TelemetryTier.ERRORS_ONLY),
            ("full", TelemetryTier.FULL),
        ],
        ids=["off", "errors-only", "full"],
    )
    def test_action_writes_matching_tier(self, cli, xdg_home, action, tier) -> None:
        result = cli("telemetry", action)
        assert result.code == 0
        # Side effect: the persisted tier matches the chosen action exactly. A
        # swapped action→tier mapping in _SetTierOp subclasses fails here.
        assert _state(xdg_home)["tier"] == tier.value
        # And the printed confirmation echoes that tier.
        assert tier.value in result.out

    @pytest.mark.parametrize(
        "action, tier",
        [
            ("off", TelemetryTier.OFF),
            ("errors-only", TelemetryTier.ERRORS_ONLY),
            ("full", TelemetryTier.FULL),
        ],
        ids=["off", "errors-only", "full"],
    )
    def test_action_marks_banner_shown(self, cli, xdg_home, action, tier) -> None:
        cli("telemetry", action)
        assert _state(xdg_home)["banner_shown"] is True


# ─── status action ──────────────────────────────────────────────────────


class TestStatusAction:
    def test_status_reports_persisted_tier(self, cli, xdg_home) -> None:
        cli("telemetry", "errors-only")
        result = cli("telemetry", "status", "--format", "json")
        assert result.code == 0
        payload = json.loads(result.out)
        assert payload["tier"] == "errors-only"
        assert payload["enabled"] is True
        assert payload["source"] == "config-file"

    def test_status_emits_only_hashed_install_id(self, cli, xdg_home) -> None:
        cli("telemetry", "reset")
        raw_id = (xdg_home / "install_id").read_text().strip()
        result = cli("telemetry", "status", "--format", "json")
        payload = json.loads(result.out)
        assert raw_id not in result.out
        assert payload["install_id_hashed"] != raw_id


# ─── reset action ───────────────────────────────────────────────────────


class TestResetAction:
    def test_reset_rotates_install_id_file(self, cli, xdg_home, monkeypatch) -> None:
        import briar.telemetry._config as cfg

        xdg_home.mkdir(parents=True, exist_ok=True)
        (xdg_home / "install_id").write_text("INSTALL-ID-PLACEHOLDER-old")
        monkeypatch.setattr(cfg, "_INSTALL_ID_CACHE", None)

        result = cli("telemetry", "reset")
        assert result.code == 0
        new_id = (xdg_home / "install_id").read_text().strip()
        assert new_id != "INSTALL-ID-PLACEHOLDER-old"


# ─── preview action + its ONE flag: --for-command ───────────────────────


class TestPreviewForCommandFlag:
    def test_for_command_override_reaches_event(self, cli, xdg_home) -> None:
        result = cli("telemetry", "preview", "--for-command", "agent.implement")
        assert result.code == 0
        event = json.loads(result.out)
        # The flag drives the rendered event's command label exactly.
        assert event["command"] == "agent.implement"

    def test_for_command_default_label(self, cli, xdg_home) -> None:
        result = cli("telemetry", "preview")
        assert result.code == 0
        event = json.loads(result.out)
        assert event["command"] == "(preview)"

    def test_preview_event_marked_not_sent(self, cli, xdg_home) -> None:
        # A preview must be distinguishable from a real run.
        result = cli("telemetry", "preview", "--for-command", "secrets.doctor")
        event = json.loads(result.out)
        assert event["outcome"] == "preview"

    @pytest.mark.parametrize("label", ["plan.run", "extract.github", "telemetry.status"])
    def test_for_command_various_labels(self, cli, xdg_home, label) -> None:
        result = cli("telemetry", "preview", "--for-command", label)
        assert json.loads(result.out)["command"] == label

    def test_preview_does_not_persist_a_tier(self, cli, xdg_home, mocker) -> None:
        # preview is read-only — it must not call save_tier. (The CLI startup
        # banner may write telemetry.json, so we assert the op's own behaviour
        # at the save_tier seam rather than file (non)existence.)
        save = mocker.patch("briar.commands.telemetry.save_tier")
        result = cli("telemetry", "preview")
        assert result.code == 0
        save.assert_not_called()
