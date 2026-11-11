"""EnvFileStore — read/write/delete/list against os.environ + file."""

from __future__ import annotations

import os

import pytest

from briar.credentials.envfile import EnvFileStore


@pytest.fixture
def envfile_root(tmp_path, monkeypatch):
    """Point BRIAR_SECRETS_FILE at a per-test tmp file."""
    path = tmp_path / "secrets.env"
    monkeypatch.setenv("BRIAR_SECRETS_FILE", str(path))
    return path


class TestRead:
    def test_read_returns_env_value(self, envfile_root, monkeypatch) -> None:
        monkeypatch.setenv("MY_VAR", "value")
        assert EnvFileStore().read("MY_VAR") == "value"

    def test_read_unset_returns_none(self, envfile_root) -> None:
        # Per the CredentialStore protocol, read() returns None (not "")
        # for a missing name — distinguishing "not set" from "set to
        # empty string" so callers can fail closed on auth errors.
        assert EnvFileStore().read("NEVER_SET") is None


class TestWrite:
    def test_write_updates_environ(self, envfile_root) -> None:
        EnvFileStore().write("MY_VAR", "value")
        assert os.environ["MY_VAR"] == "value"

    def test_write_creates_file_with_kv(self, envfile_root) -> None:
        EnvFileStore().write("MY_VAR", "value")
        assert envfile_root.exists()
        assert "MY_VAR=value" in envfile_root.read_text()

    def test_write_idempotent_replaces_existing_line(self, envfile_root) -> None:
        s = EnvFileStore()
        s.write("MY_VAR", "first")
        s.write("MY_VAR", "second")
        contents = envfile_root.read_text()
        assert "first" not in contents
        assert "MY_VAR=second" in contents
        # Only one line for MY_VAR
        assert contents.count("MY_VAR=") == 1

    def test_write_file_permissions_600(self, envfile_root) -> None:
        EnvFileStore().write("MY_VAR", "value")
        mode = envfile_root.stat().st_mode & 0o777
        assert mode == 0o600

    def test_write_creates_parent_dir(self, tmp_path, monkeypatch) -> None:
        # Path with non-existent parent
        target = tmp_path / "nested" / "deep" / "secrets.env"
        monkeypatch.setenv("BRIAR_SECRETS_FILE", str(target))
        EnvFileStore().write("MY_VAR", "value")
        assert target.exists()

    def test_write_validates_name_uppercase(self, envfile_root) -> None:
        with pytest.raises(ValueError, match="invalid env-var name"):
            EnvFileStore().write("lowercase", "value")

    @pytest.mark.parametrize("name", ["1FIRST_DIGIT", "WITH-DASH", "with space"])
    def test_write_rejects_invalid_names(self, envfile_root, name) -> None:
        with pytest.raises(ValueError):
            EnvFileStore().write(name, "value")


class TestList:
    def test_list_returns_known_prefix_keys_only(self, envfile_root, monkeypatch) -> None:
        monkeypatch.setenv("AWS_X_ACCESS_KEY_ID", "x")  # a 1.x name briar no longer reads
        monkeypatch.setenv("BRIAR_X_JIRA_TOKEN", "PLACEHOLDER-not-a-secret")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")  # a 1.x name briar no longer reads
        monkeypatch.setenv("UNRELATED_VAR", "x")  # not a credential prefix
        names = EnvFileStore().list()
        assert "AWS_X_ACCESS_KEY_ID" not in names
        assert "BRIAR_X_JIRA_TOKEN" in names
        assert "GITHUB_TOKEN" not in names
        assert "UNRELATED_VAR" not in names
