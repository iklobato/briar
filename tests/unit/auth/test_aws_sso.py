"""AwsSsoAcquirer — IAM Identity Center OIDC device-code flow.

boto3 IS a core dependency, so we mock the boto3 ``sso-oidc`` / ``sso``
client seam (``boto3.client``) and drive the operator side with the
in-repo MockPromptIO. No network, no real sleeps.

The OIDC dance modelled here follows the documented SSO OIDC API:
  - RegisterClient            -> clientId / clientSecret
    https://docs.aws.amazon.com/singlesignon/latest/OIDCAPIReference/API_RegisterClient.html
  - StartDeviceAuthorization  -> deviceCode / userCode / verificationUri
    https://docs.aws.amazon.com/singlesignon/latest/OIDCAPIReference/API_StartDeviceAuthorization.html
  - CreateToken (polled)      -> accessToken; AuthorizationPendingException
    while the user hasn't approved yet
    https://docs.aws.amazon.com/singlesignon/latest/OIDCAPIReference/API_CreateToken.html
  - sso.GetRoleCredentials    -> roleCredentials{accessKeyId,secretAccessKey,
    sessionToken,expiration(ms-since-epoch)}
    https://docs.aws.amazon.com/singlesignon/latest/PortalAPIReference/API_GetRoleCredentials.html

NB: every credential value below is an obvious non-secret PLACEHOLDER.
The response *shape* (field names / types, ms-epoch expiration) is what's
modelled on the docs — never the secret values.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError

from briar.auth._acquirers.aws_sso import AwsSsoAcquirer
from briar.auth._prompt import MockPromptIO
from briar.env_vars import CredEnv

_EXPIRATION_MS = 2_000_000_000_000  # ms since epoch → 2033-05-18T03:33:20Z
_ROLE_CREDS = {
    "accessKeyId": "AKIA-PLACEHOLDER-not-a-real-key",
    "secretAccessKey": "SECRET-PLACEHOLDER-not-a-real-secret",
    "sessionToken": "SESSION-TOKEN-PLACEHOLDER-not-a-secret",
    "expiration": _EXPIRATION_MS,
}


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **_kwargs):
        return list(self._pages)


class _FakeOidc:
    """Stand-in for the boto3 ``sso-oidc`` client.

    ``token_outcomes`` is consumed one-per ``create_token`` call: a dict
    is returned; a ClientError instance is raised; any other Exception is
    raised. This lets a test script pending→success or a terminal error.
    """

    def __init__(self, *, device=None, token_outcomes=None):
        self._device = device or {
            "deviceCode": "DEVICE-CODE-PLACEHOLDER-not-a-secret",
            "userCode": "WDJB-MJHT",
            "verificationUri": "https://device.sso.us-east-1.amazonaws.com/",
            "verificationUriComplete": "https://device.sso.us-east-1.amazonaws.com/?user_code=WDJB-MJHT",
            "interval": 5,
            "expiresIn": 600,
        }
        self._token_outcomes = list(token_outcomes or [{"accessToken": "ACCESS-TOKEN-PLACEHOLDER-not-a-secret"}])
        self.register_calls = []
        self.start_calls = []
        self.token_calls = []

    def register_client(self, **kwargs):
        self.register_calls.append(kwargs)
        return {"clientId": "CLIENT-ID-PLACEHOLDER", "clientSecret": "CLIENT-SECRET-PLACEHOLDER-not-a-secret"}

    def start_device_authorization(self, **kwargs):
        self.start_calls.append(kwargs)
        return dict(self._device)

    def create_token(self, **kwargs):
        self.token_calls.append(kwargs)
        outcome = self._token_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeSso:
    """Stand-in for the boto3 ``sso`` client."""

    def __init__(self, *, accounts=None, roles=None, role_creds=None):
        self._accounts = accounts if accounts is not None else [{"accountId": "111111111111", "accountName": "prod"}]
        self._roles = roles if roles is not None else [{"roleName": "ReadOnly"}]
        self._role_creds = role_creds if role_creds is not None else dict(_ROLE_CREDS)
        self.get_role_credentials_calls = []

    def get_paginator(self, op):
        if op == "list_accounts":
            return _FakePaginator([{"accountList": self._accounts}])
        if op == "list_account_roles":
            return _FakePaginator([{"roleList": self._roles}])
        raise AssertionError(f"unexpected paginator op {op!r}")

    def get_role_credentials(self, **kwargs):
        self.get_role_credentials_calls.append(kwargs)
        return {"roleCredentials": dict(self._role_creds)}


def _client_error(code: str) -> ClientError:
    """Build a botocore ClientError carrying the given Error.Code, matching
    the documented error envelope the OIDC CreateToken endpoint returns."""
    return ClientError({"Error": {"Code": code, "Message": code}}, "CreateToken")


@pytest.fixture
def boto3_client(mocker):
    """Patch ``boto3.client`` to vend our fakes keyed by service name.

    The fixture exposes ``.set(oidc=..., sso=...)`` so each test installs
    the fakes it wants; ``.oidc`` / ``.sso`` read them back for assertions.
    """

    class _Router:
        def __init__(self):
            self.oidc = _FakeOidc()
            self.sso = _FakeSso()
            self.client_calls = []

        def __call__(self, service, **kwargs):
            self.client_calls.append((service, kwargs))
            if service == "sso-oidc":
                return self.oidc
            if service == "sso":
                return self.sso
            raise AssertionError(f"unexpected boto3 client {service!r}")

    router = _Router()
    mocker.patch("boto3.client", side_effect=router)
    return router


def _acquire(prompt, company="acme"):
    return AwsSsoAcquirer().acquire(company=company, prompt=prompt)


class TestHappyPath:
    def test_single_account_single_role_returns_role_credentials(self, boto3_client) -> None:
        # Answers: start URL, SSO region, default-region override (blank → sso_region).
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=1)
        creds = _acquire(prompt)

        assert creds.provider_kind == "aws-sso"
        # The AWS_* entries must come from the mocked role-cred response,
        # not from any other step's payload.
        assert creds.entries[CredEnv.AWS_KEY_ID.for_company("acme")] == _ROLE_CREDS["accessKeyId"]
        assert creds.entries[CredEnv.AWS_SECRET.for_company("acme")] == _ROLE_CREDS["secretAccessKey"]
        assert creds.entries[CredEnv.AWS_SESSION.for_company("acme")] == _ROLE_CREDS["sessionToken"]
        assert creds.entries[CredEnv.AWS_REGION.for_company("acme")] == "us-east-1"

    def test_expiration_ms_converted_to_utc_datetime(self, boto3_client) -> None:
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=1)
        creds = _acquire(prompt)
        # SSO returns expiration in ms-since-epoch; the acquirer divides by 1000.
        assert creds.expires_at == datetime.fromtimestamp(_EXPIRATION_MS / 1000, tz=timezone.utc)

    def test_metadata_records_account_role_and_urls(self, boto3_client) -> None:
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=1)
        creds = _acquire(prompt)
        assert creds.metadata["auth_mode"] == "sso-device"
        assert creds.metadata["account_id"] == "111111111111"
        assert creds.metadata["role_name"] == "ReadOnly"
        assert creds.metadata["start_url"] == "https://acme.awsapps.com/start"
        assert creds.metadata["sso_region"] == "us-east-1"

    def test_device_authorization_uses_registered_client_and_start_url(self, boto3_client) -> None:
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=1)
        _acquire(prompt)
        start = boto3_client.oidc.start_calls[0]
        assert start["clientId"] == "CLIENT-ID-PLACEHOLDER"
        assert start["startUrl"] == "https://acme.awsapps.com/start"
        # create_token must use the device-code grant + the device's own code.
        token_call = boto3_client.oidc.token_calls[0]
        assert token_call["grantType"] == "urn:ietf:params:oauth:grant-type:device_code"
        assert token_call["deviceCode"] == "DEVICE-CODE-PLACEHOLDER-not-a-secret"

    def test_role_credentials_exchanged_with_token_account_and_role(self, boto3_client) -> None:
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=1)
        _acquire(prompt)
        call = boto3_client.sso.get_role_credentials_calls[0]
        assert call["accessToken"] == "ACCESS-TOKEN-PLACEHOLDER-not-a-secret"
        assert call["accountId"] == "111111111111"
        assert call["roleName"] == "ReadOnly"

    def test_user_is_shown_verification_url_and_code(self, boto3_client) -> None:
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=1)
        _acquire(prompt)
        joined = "\n".join(prompt.info_log)
        assert "WDJB-MJHT" in joined  # userCode surfaced
        # The complete verification URI is preferred and opened in the browser.
        assert prompt.opened_urls == ["https://device.sso.us-east-1.amazonaws.com/?user_code=WDJB-MJHT"]

    def test_default_region_falls_back_to_sso_region_when_blank(self, boto3_client) -> None:
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "eu-central-1", ""], poll_attempts=1)
        creds = _acquire(prompt)
        assert creds.entries[CredEnv.AWS_REGION.for_company("acme")] == "eu-central-1"

    def test_explicit_default_region_override_is_used(self, boto3_client) -> None:
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", "ap-southeast-2"], poll_attempts=1)
        creds = _acquire(prompt)
        assert creds.entries[CredEnv.AWS_REGION.for_company("acme")] == "ap-southeast-2"

    def test_blank_sso_region_defaults_to_us_east_1(self, boto3_client) -> None:
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "  ", ""], poll_attempts=1)
        creds = _acquire(prompt)
        assert creds.metadata["sso_region"] == "us-east-1"


class TestPollingRetry:
    def test_authorization_pending_then_success(self, boto3_client) -> None:
        # First create_token call raises AuthorizationPending → treated as
        # "not yet"; second returns the token. poll_attempts=2 to allow both.
        boto3_client.oidc = _FakeOidc(
            token_outcomes=[
                _client_error("AuthorizationPendingException"),
                {"accessToken": "ACCESS-TOKEN-PLACEHOLDER-not-a-secret"},
            ]
        )
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=2)
        creds = _acquire(prompt)
        assert creds.entries[CredEnv.AWS_KEY_ID.for_company("acme")] == _ROLE_CREDS["accessKeyId"]
        assert len(boto3_client.oidc.token_calls) == 2

    def test_slow_down_is_treated_as_pending(self, boto3_client) -> None:
        boto3_client.oidc = _FakeOidc(
            token_outcomes=[
                _client_error("SlowDownException"),
                {"accessToken": "ACCESS-TOKEN-PLACEHOLDER-not-a-secret"},
            ]
        )
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=2)
        creds = _acquire(prompt)
        assert creds.entries[CredEnv.AWS_KEY_ID.for_company("acme")] == _ROLE_CREDS["accessKeyId"]

    def test_unexpected_exception_during_poll_is_swallowed_as_pending(self, boto3_client) -> None:
        # Non-ClientError exceptions are logged + treated as "keep polling".
        boto3_client.oidc = _FakeOidc(
            token_outcomes=[
                RuntimeError("transient blip"),
                {"accessToken": "ACCESS-TOKEN-PLACEHOLDER-not-a-secret"},
            ]
        )
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=2)
        creds = _acquire(prompt)
        assert creds.entries[CredEnv.AWS_KEY_ID.for_company("acme")] == _ROLE_CREDS["accessKeyId"]

    def test_timeout_when_authorisation_never_completes(self, boto3_client) -> None:
        # Always pending → MockPromptIO exhausts attempts → TimeoutError,
        # which acquire() converts to a friendly RuntimeError.
        boto3_client.oidc = _FakeOidc(token_outcomes=[_client_error("AuthorizationPendingException")])
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=1)
        with pytest.raises(RuntimeError, match="timed out waiting for authorisation"):
            _acquire(prompt)


class TestSelection:
    def test_multiple_accounts_prompts_for_selection(self, boto3_client) -> None:
        boto3_client.sso = _FakeSso(
            accounts=[
                {"accountId": "111111111111", "accountName": "prod"},
                {"accountId": "222222222222", "accountName": "dev"},
            ]
        )
        # Extra answer "2" selects the second account (1-indexed).
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", "2", ""], poll_attempts=1)
        creds = _acquire(prompt)
        assert creds.metadata["account_id"] == "222222222222"

    def test_multiple_roles_prompts_for_selection(self, boto3_client) -> None:
        boto3_client.sso = _FakeSso(roles=[{"roleName": "ReadOnly"}, {"roleName": "Admin"}])
        # Extra answer "2" selects Admin (1-indexed).
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", "2", ""], poll_attempts=1)
        creds = _acquire(prompt)
        assert creds.metadata["role_name"] == "Admin"


class TestFailureModes:
    def test_no_company_raises_before_any_boto3(self, boto3_client) -> None:
        with pytest.raises(ValueError, match="--company is required"):
            _acquire(MockPromptIO(answers=[]), company="")
        assert boto3_client.client_calls == []

    def test_blank_start_url_raises(self, boto3_client) -> None:
        prompt = MockPromptIO(answers=["   ", "us-east-1"], poll_attempts=1)
        with pytest.raises(ValueError, match="SSO start URL required"):
            _acquire(prompt)

    def test_terminal_oidc_error_propagates(self, boto3_client) -> None:
        # A non-pending ClientError (e.g. expired device code) is re-raised
        # from _poll_once, not swallowed.
        boto3_client.oidc = _FakeOidc(token_outcomes=[_client_error("ExpiredTokenException")])
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=1)
        with pytest.raises(ClientError):
            _acquire(prompt)

    def test_no_accounts_visible_raises(self, boto3_client) -> None:
        boto3_client.sso = _FakeSso(accounts=[])
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=1)
        with pytest.raises(RuntimeError, match="no accounts visible"):
            _acquire(prompt)

    def test_no_roles_for_account_raises(self, boto3_client) -> None:
        boto3_client.sso = _FakeSso(roles=[])
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=1)
        with pytest.raises(RuntimeError, match="no roles for account"):
            _acquire(prompt)

    def test_missing_session_token_yields_empty_string_entry(self, boto3_client) -> None:
        # Some role-cred responses omit sessionToken → the acquirer stores "".
        creds_no_session = {k: v for k, v in _ROLE_CREDS.items() if k != "sessionToken"}
        boto3_client.sso = _FakeSso(role_creds=creds_no_session)
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=1)
        creds = _acquire(prompt)
        assert creds.entries[CredEnv.AWS_SESSION.for_company("acme")] == ""

    def test_missing_expiration_yields_no_expiry(self, boto3_client) -> None:
        creds_no_exp = {k: v for k, v in _ROLE_CREDS.items() if k != "expiration"}
        boto3_client.sso = _FakeSso(role_creds=creds_no_exp)
        prompt = MockPromptIO(answers=["https://acme.awsapps.com/start", "us-east-1", ""], poll_attempts=1)
        creds = _acquire(prompt)
        assert creds.expires_at is None


class TestRefreshAndWrites:
    def test_refresh_raises_credential_expired(self) -> None:
        from briar.auth._acquirer import CredentialExpired, Credentials

        existing = Credentials(provider_kind="aws-sso", entries={})
        with pytest.raises(CredentialExpired, match="refresh not implemented"):
            AwsSsoAcquirer().refresh(company="acme", existing=existing)

    def test_writes_declares_all_four_vars(self) -> None:
        assert AwsSsoAcquirer.writes(company="acme") == [
            CredEnv.AWS_KEY_ID.for_company("acme"),
            CredEnv.AWS_SECRET.for_company("acme"),
            CredEnv.AWS_SESSION.for_company("acme"),
            CredEnv.AWS_REGION.for_company("acme"),
        ]

    def test_writes_with_empty_company_is_empty(self) -> None:
        assert AwsSsoAcquirer.writes(company="") == []
