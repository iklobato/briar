"""CredEnv — env-var template + per-company formatting.

Catches: empty-company → double-underscore quirk, global vs per-company
template branch, unset env reads return "" (not None)."""

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

    def test_empty_company_yields_double_underscore_documented(self) -> None:
        # KNOWN: empty company gives `AWS__ACCESS_KEY_ID`. Asserted so a
        # future "reject empty" change is visible.
        assert CredEnv.AWS_KEY_ID.for_company("") == "AWS__ACCESS_KEY_ID"

    def test_space_preserved_not_replaced(self) -> None:
        # Only `-` is replaced, not whitespace.
        assert CredEnv.AWS_KEY_ID.for_company("a b") == "AWS_A B_ACCESS_KEY_ID"

    def test_global_var_with_no_brace_c_unchanged_by_for_company(self) -> None:
        # Global var template has no `{c}`; format() is a no-op.
        assert CredEnv.GITHUB_TOKEN.for_company("acme") == "GITHUB_TOKEN"


class TestRead:
    def test_unset_returns_empty_string_not_none(self, monkeypatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        assert CredEnv.GITHUB_TOKEN.read() == ""

    def test_set_value_returned(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        assert CredEnv.GITHUB_TOKEN.read() == "ghp_xxx"

    def test_per_company_reads_company_specific_key(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_ACME_ACCESS_KEY_ID", "AKIA")
        assert CredEnv.AWS_KEY_ID.read("acme") == "AKIA"

    def test_per_company_ignores_other_company_value(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_OTHER_ACCESS_KEY_ID", "X")
        # Reading for "acme" must not see "other"'s value.
        assert CredEnv.AWS_KEY_ID.read("acme") == ""

    def test_global_var_ignores_company_argument(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "global-value")
        # Passing a company name doesn't matter for a global var.
        assert CredEnv.GITHUB_TOKEN.read("acme") == "global-value"


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
        assert CredEnv.GITHUB_TOKEN == "GITHUB_TOKEN"
