"""The shared dry-run/execute gate."""

from __future__ import annotations

from briar.service import GateMode, GateResult


def test_from_confirm_maps_bool_to_mode() -> None:
    assert GateMode.from_confirm(True) is GateMode.EXECUTE
    assert GateMode.from_confirm(False) is GateMode.DRY_RUN


def test_previewed_did_not_execute() -> None:
    r = GateResult.previewed("would do X")
    assert r.mode is GateMode.DRY_RUN
    assert r.executed is False
    assert r.result is None
    assert "would do X" in r.summary


def test_performed_executed_with_result() -> None:
    r = GateResult.performed("did X", {"k": 1})
    assert r.mode is GateMode.EXECUTE
    assert r.executed is True
    assert r.result == {"k": 1}


def test_as_dict_is_json_friendly() -> None:
    d = GateResult.performed("did X", {"k": 1}).as_dict()
    assert d == {"mode": "execute", "executed": True, "summary": "did X", "result": {"k": 1}}
