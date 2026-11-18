"""`SecretGuard`: what an extractor may write about a setting of someone infrastructure (decision E7).

The rule is key NAMES only, never values: a name that looks like a secret hides any value, a value that
looks like a credential is hidden whatever its name, and a credential inside a URL is cut out of it."""

from __future__ import annotations

import pytest

from briar.extract._secrets import SecretGuard

HIDDEN = "(set)"
FAKE_VALUE = "FAKE-not-a-real-secret"
FAKE_PAT = "ghp_" + "a" * 36


@pytest.fixture
def guard() -> SecretGuard:
    return SecretGuard.standard()


class TestSecretGuard:
    @pytest.mark.parametrize(
        "name",
        [
            "admin_password",
            "DB_PASSWORD",
            "client_secret",
            "sas_token",
            "api_key",
            "access_key",
            "primary_connection_string",
            "private_key",
            "credentials",
            "oauth_token",
        ],
    )
    def test_a_name_that_looks_like_a_secret_hides_its_value(self, guard: SecretGuard, name: str) -> None:
        assert guard.shown(name, FAKE_VALUE) == HIDDEN

    @pytest.mark.parametrize("name", ["location", "storage_account_name", "container_name", "environment", "key", "resource_group_name", "bypass_cache"])
    def test_a_plain_name_keeps_its_value(self, guard: SecretGuard, name: str) -> None:
        assert guard.shown(name, "prod.tfstate") == "prod.tfstate"

    def test_a_value_that_looks_like_a_credential_is_hidden_under_a_plain_name(self, guard: SecretGuard) -> None:
        assert guard.shown("note", FAKE_PAT) == HIDDEN

    def test_a_credential_inside_a_url_is_cut_out_of_it(self, guard: SecretGuard) -> None:
        source = f"git::https://user:{FAKE_VALUE}@host.example/mods.git//net?ref=v1"

        assert guard.shown("source", source) == "git::https://host.example/mods.git//net?ref=v1"

    def test_a_url_with_no_credential_is_untouched(self, guard: SecretGuard) -> None:
        assert guard.shown("source", "git::https://host.example/mods.git//net?ref=v1") == "git::https://host.example/mods.git//net?ref=v1"

    def test_a_name_is_secret_only_by_its_name(self, guard: SecretGuard) -> None:
        assert (guard.is_secret_name("admin_password"), guard.is_secret_name("location")) == (True, False)
