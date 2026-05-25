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

    def test_read_unset_returns_empty(self, envfile_root) -> None:
        assert EnvFileStore().read("NEVER_SET") == ""


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


class TestDelete:
    def test_delete_removes_from_env_and_file(self, envfile_root) -> None:
        s = EnvFileStore()
        s.write("MY_VAR", "value")
        assert s.delete("MY_VAR") is True
        assert "MY_VAR" not in os.environ
        assert "MY_VAR=" not in envfile_root.read_text()

    def test_delete_missing_returns_false(self, envfile_root) -> None:
        assert EnvFileStore().delete("NEVER_SET") is False

    def test_delete_env_only_returns_true(self, envfile_root, monkeypatch) -> None:
        # In env but not in file
        monkeypatch.setenv("MY_VAR", "value")
        assert EnvFileStore().delete("MY_VAR") is True


class TestList:
    def test_list_returns_known_prefix_keys_only(self, envfile_root, monkeypatch) -> None:
        monkeypatch.setenv("AWS_X_ACCESS_KEY_ID", "x")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
        monkeypatch.setenv("UNRELATED_VAR", "x")  # not a credential prefix
        names = EnvFileStore().list()
        assert "AWS_X_ACCESS_KEY_ID" in names
        assert "GITHUB_TOKEN" in names
        assert "UNRELATED_VAR" not in names


class TestExpiresAt:
    def test_expires_at_returns_empty_string(self, envfile_root) -> None:
        # EnvFileStore doesn't track expiry; returns empty.
        assert EnvFileStore().expires_at("MY_VAR") == ""
