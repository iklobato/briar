"""Update-check: throttle, opt-out, version compare, swallow-on-error."""

from __future__ import annotations

import briar.update_check as uc


def test_newer_version_produces_notice(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _clear_optouts(monkeypatch)
    monkeypatch.setattr(uc, "_fetch_latest", lambda: "1.2.0")
    notice = uc.maybe_notify("1.1.46", now=1000.0)
    assert notice is not None
    assert "1.1.46 -> 1.2.0" in notice


def test_same_or_older_no_notice(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _clear_optouts(monkeypatch)
    monkeypatch.setattr(uc, "_fetch_latest", lambda: "1.1.46")
    assert uc.maybe_notify("1.1.46", now=1000.0) is None


def test_throttle_uses_cache_and_skips_network(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _clear_optouts(monkeypatch)
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return "2.0.0"

    monkeypatch.setattr(uc, "_fetch_latest", fetch)
    uc.maybe_notify("1.0.0", now=1000.0)  # first call: fetches + caches
    uc.maybe_notify("1.0.0", now=1000.0 + 60)  # within 24h: no second fetch
    assert calls["n"] == 1


def test_opt_out_envs_short_circuit(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(uc, "_fetch_latest", lambda: (_ for _ in ()).throw(AssertionError("must not fetch")))
    for var in ("BRIAR_NO_UPDATE_CHECK", "DO_NOT_TRACK"):
        _clear_optouts(monkeypatch)
        monkeypatch.setenv(var, "1")
        assert uc.maybe_notify("1.0.0", now=1000.0) is None
    _clear_optouts(monkeypatch)
    monkeypatch.setenv("BRIAR_TELEMETRY", "off")
    assert uc.maybe_notify("1.0.0", now=1000.0) is None


def test_network_failure_is_swallowed(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _clear_optouts(monkeypatch)
    monkeypatch.setattr(uc, "_fetch_latest", lambda: None)  # simulates a failed lookup
    assert uc.maybe_notify("1.1.46", now=1000.0) is None


def test_non_numeric_versions_dont_crash():
    assert uc._is_newer("1.2.0rc1", "1.1.0") is False
    assert uc._is_newer("1.2.0", "1.1.0") is True


def _clear_optouts(monkeypatch):
    for var in ("BRIAR_NO_UPDATE_CHECK", "DO_NOT_TRACK", "BRIAR_TELEMETRY"):
        monkeypatch.delenv(var, raising=False)
