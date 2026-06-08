"""JiraSessionAcquirer — browser-session-cookie paste flow.

No network: the acquirer only talks to the operator via PromptIO and
parses the pasted JWT locally. We drive it with MockPromptIO and assert
the produced Credentials bundle, URL normalisation, and JWT-exp parsing.

NB: the ``tenant.session.token`` values are obvious non-secret
PLACEHOLDERS. Where a real JWT shape is required (to exercise exp
parsing) we build one at runtime from a tiny header/payload so no
secret-shaped literal lands in the diff.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest

from briar.auth._acquirers.jira_session import JiraSessionAcquirer, _decode_jwt_exp, _normalize_jira_url
from briar.auth._prompt import MockPromptIO
from briar.env_vars import CredEnv


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_jwt(payload: dict) -> str:
    """Build a 3-segment JWT with the given payload. The signature is a
    throwaway placeholder — the acquirer only reads the payload, never
    verifies the signature."""
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(payload).encode())
    return f"{header}.{body}.SIGNATURE-PLACEHOLDER-not-a-secret"


_PLACEHOLDER_TOKEN = "TENANT-SESSION-TOKEN-PLACEHOLDER-not-a-secret"


class TestNormalizeUrl:
    def test_strips_path_and_query(self) -> None:
        assert _normalize_jira_url("https://acme.atlassian.net/jira/your-work?x=1") == "https://acme.atlassian.net"

    def test_adds_https_scheme_when_missing(self) -> None:
        assert _normalize_jira_url("acme.atlassian.net/jira") == "https://acme.atlassian.net"

    def test_blank_returns_empty(self) -> None:
        # Covers the early-return: genuinely blank input stays "" so the
        # caller's emptiness check fires.
        assert _normalize_jira_url("") == ""
        assert _normalize_jira_url("   ") == ""

    def test_unrecognisable_input_falls_back_to_rstrip(self) -> None:
        # Input carries `://` (so no https:// is prepended) but urlsplit
        # yields an empty netloc → the acquirer falls back to a plain
        # strip-and-rstrip rather than crashing. The `if not parts.netloc`
        # branch; trailing slash is stripped.
        assert _normalize_jira_url("https:///onlypath/") == "https:///onlypath"


class TestDecodeJwtExp:
    def test_extracts_exp_as_utc_datetime(self) -> None:
        exp = 2_000_000_000  # 2033-05-18T03:33:20Z
        token = _make_jwt({"exp": exp, "sub": "u"})
        out = _decode_jwt_exp(token)
        assert out == datetime.fromtimestamp(exp, tz=timezone.utc)

    def test_non_three_segment_returns_none(self) -> None:
        assert _decode_jwt_exp("only.two") is None

    def test_malformed_payload_returns_none(self) -> None:
        # Middle segment isn't valid base64-json → tolerated, returns None.
        assert _decode_jwt_exp("aaa.!!!notb64!!!.ccc") is None

    def test_missing_exp_returns_none(self) -> None:
        assert _decode_jwt_exp(_make_jwt({"sub": "u"})) is None

    def test_non_int_exp_returns_none(self) -> None:
        assert _decode_jwt_exp(_make_jwt({"exp": "soon"})) is None


class TestAcquire:
    def test_returns_session_credentials_and_normalises_url(self) -> None:
        token = _make_jwt({"exp": 2_000_000_000})
        prompt = MockPromptIO(answers=["https://acme.atlassian.net/jira/your-work", token])
        creds = JiraSessionAcquirer().acquire(company="acme", prompt=prompt)

        assert creds.provider_kind == "jira-session"
        assert creds.metadata == {"auth_mode": "browser-session"}
        assert creds.entries[CredEnv.JIRA_URL.for_company("acme")] == "https://acme.atlassian.net"
        assert creds.entries[CredEnv.JIRA_TENANT_SESSION_TOKEN.for_company("acme")] == token
        assert creds.entries[CredEnv.JIRA_AUTH_KIND.for_company("acme")] == "session"
        assert creds.expires_at == datetime.fromtimestamp(2_000_000_000, tz=timezone.utc)

    def test_non_jwt_token_yields_no_expiry(self) -> None:
        prompt = MockPromptIO(answers=["https://acme.atlassian.net", _PLACEHOLDER_TOKEN])
        creds = JiraSessionAcquirer().acquire(company="acme", prompt=prompt)
        assert creds.expires_at is None
        assert creds.entries[CredEnv.JIRA_TENANT_SESSION_TOKEN.for_company("acme")] == _PLACEHOLDER_TOKEN

    def test_token_prompt_suppresses_echo(self) -> None:
        prompt = MockPromptIO(answers=["https://acme.atlassian.net", _PLACEHOLDER_TOKEN])
        JiraSessionAcquirer().acquire(company="acme", prompt=prompt)
        token_prompts = [s for m, s in prompt.prompts if "tenant.session.token" in m]
        assert token_prompts == [True]


class TestFailureModes:
    def test_no_company_raises(self) -> None:
        prompt = MockPromptIO(answers=[])
        with pytest.raises(ValueError, match="--company is required"):
            JiraSessionAcquirer().acquire(company="", prompt=prompt)
        assert prompt.prompts == []

    def test_blank_url_raises(self) -> None:
        # URL normalises to "" → the URL+token required guard fires.
        prompt = MockPromptIO(answers=["   ", _PLACEHOLDER_TOKEN])
        with pytest.raises(ValueError, match="URL \\+ tenant.session.token required"):
            JiraSessionAcquirer().acquire(company="acme", prompt=prompt)

    def test_blank_token_raises(self) -> None:
        prompt = MockPromptIO(answers=["https://acme.atlassian.net", "   "])
        with pytest.raises(ValueError, match="URL \\+ tenant.session.token required"):
            JiraSessionAcquirer().acquire(company="acme", prompt=prompt)


class TestWrites:
    def test_writes_declares_the_three_mandatory_vars(self) -> None:
        assert JiraSessionAcquirer.writes(company="acme") == [
            CredEnv.JIRA_URL.for_company("acme"),
            CredEnv.JIRA_TENANT_SESSION_TOKEN.for_company("acme"),
            CredEnv.JIRA_AUTH_KIND.for_company("acme"),
        ]

    def test_writes_with_empty_company_is_empty(self) -> None:
        assert JiraSessionAcquirer.writes(company="") == []
