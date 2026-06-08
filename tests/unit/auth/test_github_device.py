"""GitHub OAuth device-flow acquirer.

The device flow (https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/
authorizing-oauth-apps#device-flow) is two POSTs:
  1. POST https://github.com/login/device/code     -> device_code/user_code
  2. POST https://github.com/login/oauth/access_token (polled) -> access_token

Responses below are modelled on the documented JSON bodies (Accept:
application/json form): the device-code response and the four poll outcomes
(authorization_pending, slow_down, a terminal error, and success). We mock the
module-level ``_post_form`` seam (urllib) and drive the user side with the
in-repo ``MockPromptIO`` so no network or real sleeps happen.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from briar.auth._acquirers.github_device import GithubDeviceAcquirer
from briar.auth._prompt import MockPromptIO

# Doc: device-code success body
# https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#response-parameters
# NB: device_code / access_token are obvious non-secret PLACEHOLDERS (the
# values are never validated — the tests only assert they flow through). Real
# token-shaped strings here would trip secret scanners (GitGuardian) for no
# reason. The response *shape* is what's modelled on the docs, not the values.
_DEVICE_INIT: Dict[str, Any] = {
    "device_code": "DEVICE-CODE-PLACEHOLDER-not-a-secret",
    "user_code": "WDJB-MJHT",
    "verification_uri": "https://github.com/login/device",
    "expires_in": 900,
    "interval": 5,
}
# Doc: access-token success body (Accept: application/json)
_TOKEN_OK: Dict[str, Any] = {
    "access_token": "ACCESS-TOKEN-PLACEHOLDER-not-a-secret",
    "token_type": "bearer",
    "scope": "repo,read:org",
}


@pytest.fixture
def post_form(mocker):  # type: ignore[no-untyped-def]
    """Patch the module-level _post_form; script its return values."""
    return mocker.patch("briar.auth._acquirers.github_device._post_form")


def _acquire(post_form, *, responses: List[Dict[str, Any]], poll_attempts: int = 1, client_id: str = "Iv1.abc123", monkeypatch=None):  # type: ignore[no-untyped-def]
    post_form.side_effect = responses
    if monkeypatch is not None and client_id is not None:
        monkeypatch.setenv("BRIAR_GITHUB_CLIENT_ID", client_id)
    prompt = MockPromptIO(poll_attempts=poll_attempts)
    creds = GithubDeviceAcquirer().acquire(company="acme", prompt=prompt)
    return creds, prompt


class TestHappyPath:
    def test_returns_token_credentials(self, post_form, monkeypatch) -> None:
        creds, prompt = _acquire(post_form, responses=[_DEVICE_INIT, _TOKEN_OK], monkeypatch=monkeypatch)
        # The token from poll step 2 ends up as GITHUB_TOKEN — a swapped
        # field (e.g. returning device_code) would fail this.
        assert creds.entries["GITHUB_TOKEN"] == _TOKEN_OK["access_token"]
        assert creds.provider_kind == "github-device"
        assert creds.metadata["auth_mode"] == "oauth-device"
        assert creds.metadata["scopes"] == "repo,read:org"

    def test_first_post_requests_device_code_with_client_id_and_scope(self, post_form, monkeypatch) -> None:
        _acquire(post_form, responses=[_DEVICE_INIT, _TOKEN_OK], client_id="Iv1.zzz", monkeypatch=monkeypatch)
        url, fields = post_form.call_args_list[0].args
        assert url == "https://github.com/login/device/code"
        assert fields["client_id"] == "Iv1.zzz"
        assert fields["scope"] == "repo,read:org"

    def test_poll_post_uses_device_code_grant(self, post_form, monkeypatch) -> None:
        _acquire(post_form, responses=[_DEVICE_INIT, _TOKEN_OK], monkeypatch=monkeypatch)
        url, fields = post_form.call_args_list[1].args
        assert url == "https://github.com/login/oauth/access_token"
        assert fields["device_code"] == _DEVICE_INIT["device_code"]
        assert fields["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"

    def test_user_is_shown_the_code_and_verification_url(self, post_form, monkeypatch) -> None:
        _, prompt = _acquire(post_form, responses=[_DEVICE_INIT, _TOKEN_OK], monkeypatch=monkeypatch)
        joined = "\n".join(prompt.info_log)
        assert "WDJB-MJHT" in joined  # the user_code must be surfaced
        assert prompt.opened_urls == ["https://github.com/login/device"]

    def test_authorization_pending_then_success(self, post_form, monkeypatch) -> None:
        # First poll: not yet authorised (returns None internally); second: token.
        creds, _ = _acquire(
            post_form,
            responses=[_DEVICE_INIT, {"error": "authorization_pending"}, _TOKEN_OK],
            poll_attempts=2,
            monkeypatch=monkeypatch,
        )
        assert creds.entries["GITHUB_TOKEN"] == _TOKEN_OK["access_token"]

    def test_slow_down_is_treated_as_pending_not_fatal(self, post_form, monkeypatch) -> None:
        creds, _ = _acquire(
            post_form,
            responses=[_DEVICE_INIT, {"error": "slow_down"}, _TOKEN_OK],
            poll_attempts=2,
            monkeypatch=monkeypatch,
        )
        assert creds.entries["GITHUB_TOKEN"] == _TOKEN_OK["access_token"]


class TestFailureModes:
    def test_missing_client_id_raises_with_guidance(self, post_form, monkeypatch) -> None:
        monkeypatch.delenv("BRIAR_GITHUB_CLIENT_ID", raising=False)
        with pytest.raises(RuntimeError, match="BRIAR_GITHUB_CLIENT_ID"):
            GithubDeviceAcquirer().acquire(company="acme", prompt=MockPromptIO())
        post_form.assert_not_called()  # bails before any HTTP

    def test_blank_client_id_is_treated_as_missing(self, post_form, monkeypatch) -> None:
        monkeypatch.setenv("BRIAR_GITHUB_CLIENT_ID", "   ")  # whitespace only
        with pytest.raises(RuntimeError, match="BRIAR_GITHUB_CLIENT_ID"):
            GithubDeviceAcquirer().acquire(company="acme", prompt=MockPromptIO())

    def test_device_code_request_failure_is_wrapped(self, post_form, monkeypatch) -> None:
        monkeypatch.setenv("BRIAR_GITHUB_CLIENT_ID", "Iv1.abc")
        post_form.side_effect = OSError("connection reset")
        with pytest.raises(RuntimeError, match="device-code request failed"):
            GithubDeviceAcquirer().acquire(company="acme", prompt=MockPromptIO())

    def test_malformed_device_code_response_raises(self, post_form, monkeypatch) -> None:
        monkeypatch.setenv("BRIAR_GITHUB_CLIENT_ID", "Iv1.abc")
        # No device_code/user_code in the body → malformed.
        post_form.side_effect = [{"interval": 5, "expires_in": 900}]
        with pytest.raises(RuntimeError, match="malformed device-code response"):
            GithubDeviceAcquirer().acquire(company="acme", prompt=MockPromptIO())

    def test_terminal_oauth_error_during_poll_aborts(self, post_form, monkeypatch) -> None:
        monkeypatch.setenv("BRIAR_GITHUB_CLIENT_ID", "Iv1.abc")
        post_form.side_effect = [
            _DEVICE_INIT,
            {"error": "access_denied", "error_description": "user cancelled"},
        ]
        prompt = MockPromptIO(poll_attempts=3)
        with pytest.raises(RuntimeError, match="OAuth error: access_denied"):
            GithubDeviceAcquirer().acquire(company="acme", prompt=prompt)

    def test_timeout_waiting_for_authorisation(self, post_form, monkeypatch) -> None:
        monkeypatch.setenv("BRIAR_GITHUB_CLIENT_ID", "Iv1.abc")
        # Always pending → MockPromptIO exhausts its attempts → TimeoutError,
        # which acquire() converts to a friendly RuntimeError.
        post_form.side_effect = [_DEVICE_INIT, {"error": "authorization_pending"}]
        prompt = MockPromptIO(poll_attempts=1)
        with pytest.raises(RuntimeError, match="timed out"):
            GithubDeviceAcquirer().acquire(company="acme", prompt=prompt)

    def test_writes_declares_github_token(self) -> None:
        assert GithubDeviceAcquirer.writes(company="acme") == ["GITHUB_TOKEN"]


class TestPostForm:
    """The urllib seam that the acquirer mocks out everywhere else."""

    def test_posts_urlencoded_form_with_json_accept_and_parses_body(self, mocker) -> None:
        import json

        from briar.auth._acquirers import github_device as mod

        captured = {}

        class _Resp:
            def read(self):  # noqa: ANN001
                return json.dumps({"device_code": "dc", "user_code": "UC"}).encode()

            def __enter__(self):  # noqa: ANN001
                return self

            def __exit__(self, *a):  # noqa: ANN001
                return None

        def _urlopen(req, timeout):  # noqa: ANN001
            captured["url"] = req.full_url
            captured["data"] = req.data
            captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
            captured["method"] = req.get_method()
            captured["timeout"] = timeout
            return _Resp()

        mocker.patch("urllib.request.urlopen", side_effect=_urlopen)
        out = mod._post_form("https://github.com/login/device/code", {"client_id": "Iv1.x", "scope": "repo,read:org"})

        assert out == {"device_code": "dc", "user_code": "UC"}  # parsed JSON returned
        assert captured["method"] == "POST"
        # x-www-form-urlencoded body, NOT json — GitHub's device endpoint wants a form.
        assert captured["data"] == b"client_id=Iv1.x&scope=repo%2Cread%3Aorg"
        assert captured["headers"].get("accept") == "application/json"
        assert captured["timeout"] == 15  # bounded — never hang the CLI forever
