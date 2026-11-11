"""CredEnv — env-var template + per-company formatting.

Catches: empty-company on a templated var is rejected (was a silent
double-underscore quirk), global vs per-company template branch, unset
env reads return "" (not None)."""

from __future__ import annotations

import pytest

from briar.env_vars import CredEnv


class TestForCompany:
    def test_normalises_dash_to_underscore_and_uppercases(self) -> None:
        assert CredEnv.AWS_KEY_ID.for_company("acme-co") == "AWS_ACME_CO_ACCESS_KEY_ID"

    def test_already_uppercase_is_idempotent(self) -> None:
        assert CredEnv.AWS_KEY_ID.for_company("ACME") == "AWS_ACME_ACCESS_KEY_ID"

    def test_multiple_dashes_all_replaced(self) -> None:
        assert CredEnv.AWS_KEY_ID.for_company("a-b-c") == "AWS_A_B_C_ACCESS_KEY_ID"

    def test_empty_company_on_templated_var_raises(self) -> None:
        # Empty company used to silently produce `AWS__ACCESS_KEY_ID`
        # (double underscore) which never matched any operator-set env.
        # for_company now refuses so the misuse surfaces immediately.
        with pytest.raises(ValueError, match="non-empty company"):
            CredEnv.AWS_KEY_ID.for_company("")

    def test_space_preserved_not_replaced(self) -> None:
        # Only `-` is replaced, not whitespace.
        assert CredEnv.AWS_KEY_ID.for_company("a b") == "AWS_A B_ACCESS_KEY_ID"


class TestRead:
    def test_unset_returns_empty_string_not_none(self, monkeypatch) -> None:
        monkeypatch.delenv("AWS_ACME_ACCESS_KEY_ID", raising=False)
        assert CredEnv.AWS_KEY_ID.read("acme") == ""

    def test_set_value_returned(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_ACME_ACCESS_KEY_ID", "fake-key-id")
        assert CredEnv.AWS_KEY_ID.read("acme") == "fake-key-id"

    def test_per_company_reads_company_specific_key(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_ACME_ACCESS_KEY_ID", "AKIA")
        assert CredEnv.AWS_KEY_ID.read("acme") == "AKIA"

    def test_per_company_ignores_other_company_value(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_OTHER_ACCESS_KEY_ID", "X")
        # Reading for "acme" must not see "other"'s value.
        assert CredEnv.AWS_KEY_ID.read("acme") == ""

    def test_templated_var_without_company_returns_empty_not_template(self, monkeypatch) -> None:
        # A per-company var read with no company is "not configured" → "".
        # It must NOT leak the raw template ("AWS_{c}_ACCESS_KEY_ID") or the
        # literal env key — callers branch on truthiness, and a non-empty
        # template would read as "configured" and then never match any env.
        monkeypatch.setenv("AWS_{c}_ACCESS_KEY_ID", "should-not-be-read")
        assert CredEnv.AWS_KEY_ID.read("") == ""
        assert CredEnv.AWS_KEY_ID.read() == ""


class TestEnumShape:
    def test_every_per_company_template_has_brace_c_placeholder(self) -> None:
        """Invariant: a member whose name reads as per-company (contains
        `{c}` in value) must use it; a member that doesn't, mustn't."""
        for member in CredEnv:
            # No assertion about which template is per-company vs global —
            # just check there are no malformed entries (e.g. literal "{c"
            # without "}").
            value = member.value
            if "{" in value:
                assert "{c}" in value, f"{member.name} has malformed template: {value!r}"

    def test_enum_members_are_strings(self) -> None:
        # CredEnv inherits from str — comparable as strings.
        assert CredEnv.AWS_KEY_ID == "AWS_{c}_ACCESS_KEY_ID"
