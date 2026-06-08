"""Pure-logic tests for messaging/_writer.py helpers + _jira_creds.py.

No external IO here — `parse_pr_target`, `with_ai_prefix`, `SendResult`
and `JiraCreds` are deterministic. These pin the contracts every writer
relies on (target parsing + the mandatory [AI] marker)."""

from __future__ import annotations

import pytest

from briar.messaging._jira_creds import JiraCreds
from briar.messaging._writer import MessageWriter, SendResult, parse_pr_target, with_ai_prefix


class TestParsePrTarget:
    def test_owner_repo_hash_number(self) -> None:
        assert parse_pr_target("owner/repo#42", {}) == ("owner/repo", 42)

    def test_workspace_repo_hash_number(self) -> None:
        assert parse_pr_target("ws/repo#7", {}) == ("ws/repo", 7)

    def test_bare_slug_uses_extras_pr(self) -> None:
        assert parse_pr_target("owner/repo", {"pr": 99}) == ("owner/repo", 99)

    def test_bare_slug_extras_pr_as_string_coerced(self) -> None:
        # extras["pr"] may arrive as a string from JSON/CLI; int() coerces.
        assert parse_pr_target("owner/repo", {"pr": "15"}) == ("owner/repo", 15)

    def test_hash_form_wins_over_extras(self) -> None:
        # The "#" branch is taken first; extras["pr"] is ignored.
        assert parse_pr_target("owner/repo#3", {"pr": 999}) == ("owner/repo", 3)

    def test_hash_with_non_numeric_number_is_malformed(self) -> None:
        assert parse_pr_target("owner/repo#abc", {}) == ("", 0)

    def test_hash_with_empty_number_is_malformed(self) -> None:
        assert parse_pr_target("owner/repo#", {}) == ("", 0)

    def test_bare_slug_without_extras_is_malformed(self) -> None:
        assert parse_pr_target("owner/repo", {}) == ("", 0)

    def test_empty_target_without_extras_is_malformed(self) -> None:
        assert parse_pr_target("", {}) == ("", 0)

    def test_empty_target_with_extras_still_malformed(self) -> None:
        # `target` is falsy → fallback branch rejects it.
        assert parse_pr_target("", {"pr": 5}) == ("", 0)

    def test_extras_pr_non_numeric_is_malformed(self) -> None:
        assert parse_pr_target("owner/repo", {"pr": "not-a-number"}) == ("", 0)

    def test_extras_pr_none_is_malformed(self) -> None:
        assert parse_pr_target("owner/repo", {"pr": None}) == ("", 0)

    def test_negative_number_preserved(self) -> None:
        # int("-1") parses; the writers separately reject falsy numbers,
        # but a negative is truthy so parse keeps it (writer's job to validate).
        assert parse_pr_target("owner/repo#-1", {}) == ("owner/repo", -1)


class TestWithAiPrefix:
    def test_prepends_marker(self) -> None:
        assert with_ai_prefix("hello world") == "[AI] hello world"

    def test_idempotent_when_already_prefixed(self) -> None:
        assert with_ai_prefix("[AI] already done") == "[AI] already done"

    def test_idempotent_respects_leading_whitespace(self) -> None:
        # lstrip() means a leading newline before [AI] still counts as marked.
        assert with_ai_prefix("\n[AI] x") == "\n[AI] x"

    def test_empty_body_passthrough(self) -> None:
        assert with_ai_prefix("") == ""

    def test_marker_not_at_start_gets_prefixed(self) -> None:
        assert with_ai_prefix("see [AI] reference") == "[AI] see [AI] reference"


class TestSendResult:
    def test_defaults(self) -> None:
        r = SendResult(ok=True)
        assert r.ok is True
        assert r.detail == ""
        assert r.ref == ""

    def test_frozen(self) -> None:
        r = SendResult(ok=True, ref="42")
        with pytest.raises(Exception):
            r.ok = False  # type: ignore[misc]

    def test_carries_ref_and_detail(self) -> None:
        r = SendResult(ok=False, detail="boom", ref="9")
        assert (r.ok, r.detail, r.ref) == (False, "boom", "9")


class TestMessageWriterBase:
    def test_required_env_vars_default_empty(self) -> None:
        assert MessageWriter.required_env_vars() == []

    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            MessageWriter()  # type: ignore[abstract]


class TestJiraCreds:
    def test_from_env_empty_company_blank(self) -> None:
        creds = JiraCreds.from_env("")
        assert creds == JiraCreds(url="", email="", token="")
        assert creds.is_complete() is False

    def test_from_env_reads_company_vars(self, monkeypatch) -> None:
        monkeypatch.setenv("JIRA_ACME_URL", "https://acme.atlassian.net")
        monkeypatch.setenv("JIRA_ACME_EMAIL", "bot@acme.com")
        monkeypatch.setenv("JIRA_ACME_TOKEN", "tok-123")
        creds = JiraCreds.from_env("acme")
        assert creds.url == "https://acme.atlassian.net"
        assert creds.email == "bot@acme.com"
        assert creds.token == "tok-123"
        assert creds.is_complete() is True

    def test_is_complete_false_when_any_missing(self, monkeypatch) -> None:
        monkeypatch.setenv("JIRA_ACME_URL", "https://acme.atlassian.net")
        monkeypatch.setenv("JIRA_ACME_EMAIL", "bot@acme.com")
        # token missing
        assert JiraCreds.from_env("acme").is_complete() is False

    def test_required_env_vars_lists_all_three(self) -> None:
        names = JiraCreds.required_env_vars("acme")
        assert names == ["JIRA_ACME_URL", "JIRA_ACME_EMAIL", "JIRA_ACME_TOKEN"]

    def test_required_env_vars_empty_without_company(self) -> None:
        assert JiraCreds.required_env_vars("") == []

    def test_client_constructs_atlassian_jira_with_creds(self, mocker) -> None:
        # Mock the atlassian.Jira constructor; assert wire args (cloud, timeout).
        jira_ctor = mocker.patch("atlassian.Jira", autospec=True)
        creds = JiraCreds(url="https://acme.atlassian.net", email="bot@acme.com", token="tok")
        creds.client()
        jira_ctor.assert_called_once_with(
            url="https://acme.atlassian.net",
            username="bot@acme.com",
            password="tok",
            cloud=True,
            timeout=20,
        )
