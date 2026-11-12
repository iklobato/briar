"""Tests for `save_runbook_file` — the inverse of `load_runbook_file`.

Assert the three guarantees: load→save→load is an identity for the model,
literal secrets in env-var-name fields are rejected (and never written), and
the write is atomic (no temp/partial file left behind, existing file preserved
on failure).
"""

from __future__ import annotations

import pytest

from briar.errors import ConfigError
from briar.iac.runbook import load_runbook_file, save_runbook_file
from briar.iac.runbook.models import RunbookFile

_FULL = {
    "version": 1,
    "companies": {
        "acme": {
            "knowledge": {"store": "file", "name": "knowledge:acme", "config": {"inventory": "true"}},
            "extract": [{"name": "pr-archaeology", "args": {"pr_repo": ["acme/web"]}}],
        }
    },
}


def test_load_save_load_roundtrips(tmp_path) -> None:
    rb = RunbookFile.model_validate(_FULL)
    out = tmp_path / "runbook.yaml"

    save_runbook_file(out, rb)
    reloaded = load_runbook_file(out)

    # The model, not the byte stream, is the contract: dumping both excludes
    # defaults the same way, so equal dumps == semantically identical config.
    assert reloaded.model_dump(exclude_defaults=True) == rb.model_dump(exclude_defaults=True)
    acme = reloaded.companies["acme"]
    assert acme.knowledge.config["inventory"] == "true"
    assert acme.extract[0].args == {"pr_repo": ["acme/web"]}


def test_returns_resolved_path(tmp_path) -> None:
    rb = RunbookFile.model_validate({"companies": {"acme": {}}})
    out = tmp_path / "nested" / "rb.yaml"
    result = save_runbook_file(out, rb)
    assert result == out
    assert out.exists()


def test_literal_secret_in_dsn_env_config_is_rejected(tmp_path) -> None:
    rb = RunbookFile.model_validate(
        {"companies": {"acme": {"knowledge": {"store": "file", "name": "k:acme", "config": {"token_env": "literal-value-PLACEHOLDER-not-a-secret"}}}}}
    )
    out = tmp_path / "rb.yaml"
    with pytest.raises(ConfigError, match="env-var NAME"):
        save_runbook_file(out, rb)
    # Fail closed: nothing written, no temp file left behind.
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []


def test_non_env_config_keys_are_not_secret_checked(tmp_path) -> None:
    # `inventory` is a behaviour flag, not an env-var name — must pass through.
    rb = RunbookFile.model_validate({"companies": {"acme": {"knowledge": {"store": "file", "name": "k:acme", "config": {"inventory": "true"}}}}})
    save_runbook_file(tmp_path / "rb.yaml", rb)
    reloaded = load_runbook_file(tmp_path / "rb.yaml")
    assert reloaded.companies["acme"].knowledge.config["inventory"] == "true"


def test_overwrite_is_atomic_and_replaces_prior(tmp_path) -> None:
    out = tmp_path / "rb.yaml"
    save_runbook_file(out, RunbookFile.model_validate({"companies": {"acme": {}}}))
    save_runbook_file(out, RunbookFile.model_validate({"companies": {"beta": {}}}))
    reloaded = load_runbook_file(out)
    assert set(reloaded.companies) == {"beta"}
    # No temp turds from either write.
    assert [p.name for p in tmp_path.iterdir()] == ["rb.yaml"]
