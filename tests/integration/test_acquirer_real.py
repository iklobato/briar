"""End-to-end: the REAL GitHub device-flow acquirer (urllib) against a wire-level
mock of github.com's device endpoints, so its real two-POST flow + polling run.

Device flow: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_github_device_flow_real_urllib(mock_api, monkeypatch) -> None:
    from briar.auth._acquirers import github_device
    from briar.auth._prompt import MockPromptIO

    # Point the acquirer's two endpoint constants at the local server.
    monkeypatch.setattr(github_device, "_DEVICE_CODE_URL", mock_api.base_url + "/login/device/code")
    monkeypatch.setattr(github_device, "_TOKEN_URL", mock_api.base_url + "/login/oauth/access_token")
    monkeypatch.setenv("BRIAR_GITHUB_CLIENT_ID", "Iv1.placeholder")

    mock_api.add(
        "POST",
        "/login/device/code",
        {
            "device_code": "DEVICE-CODE-PLACEHOLDER",
            "user_code": "WDJB-MJHT",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
        },
    )
    mock_api.add("POST", "/login/oauth/access_token", {"access_token": "ACCESS-TOKEN-PLACEHOLDER", "token_type": "bearer", "scope": "repo,read:org"})

    creds = github_device.GithubDeviceAcquirer().acquire(company="acme", prompt=MockPromptIO(poll_attempts=1))

    # Real urllib flow: both endpoints hit, token mapped to GITHUB_TOKEN.
    paths = [r["path"] for r in mock_api.received]
    assert "/login/device/code" in paths
    assert "/login/oauth/access_token" in paths
    assert creds.entries["GITHUB_TOKEN"] == "ACCESS-TOKEN-PLACEHOLDER"
    assert creds.metadata["auth_mode"] == "oauth-device"


def test_github_device_flow_pending_then_success(mock_api, monkeypatch) -> None:
    from briar.auth._acquirers import github_device
    from briar.auth._prompt import MockPromptIO

    monkeypatch.setattr(github_device, "_DEVICE_CODE_URL", mock_api.base_url + "/login/device/code")
    monkeypatch.setattr(github_device, "_TOKEN_URL", mock_api.base_url + "/login/oauth/access_token")
    monkeypatch.setenv("BRIAR_GITHUB_CLIENT_ID", "Iv1.placeholder")

    mock_api.add(
        "POST",
        "/login/device/code",
        {"device_code": "DC", "user_code": "WDJB-MJHT", "verification_uri": "https://github.com/login/device", "expires_in": 900, "interval": 1},
    )
    # First poll: authorization_pending; second: the token (sequence).
    mock_api.add("POST", "/login/oauth/access_token", {"error": "authorization_pending"})
    mock_api.add("POST", "/login/oauth/access_token", {"access_token": "ACCESS-TOKEN-PLACEHOLDER", "token_type": "bearer"})

    creds = github_device.GithubDeviceAcquirer().acquire(company="acme", prompt=MockPromptIO(poll_attempts=2))
    assert creds.entries["GITHUB_TOKEN"] == "ACCESS-TOKEN-PLACEHOLDER"


def test_github_device_terminal_oauth_error_aborts(mock_api, monkeypatch) -> None:
    from briar.auth._acquirers import github_device
    from briar.auth._prompt import MockPromptIO

    monkeypatch.setattr(github_device, "_DEVICE_CODE_URL", mock_api.base_url + "/login/device/code")
    monkeypatch.setattr(github_device, "_TOKEN_URL", mock_api.base_url + "/login/oauth/access_token")
    monkeypatch.setenv("BRIAR_GITHUB_CLIENT_ID", "Iv1.placeholder")
    mock_api.add("POST", "/login/device/code", {"device_code": "DC", "user_code": "WDJB-MJHT", "verification_uri": "https://github.com/login/device", "expires_in": 900, "interval": 1})
    mock_api.add("POST", "/login/oauth/access_token", {"error": "access_denied", "error_description": "user cancelled"})

    with pytest.raises(RuntimeError, match="access_denied"):
        github_device.GithubDeviceAcquirer().acquire(company="acme", prompt=MockPromptIO(poll_attempts=3))


def test_github_device_device_code_5xx_is_wrapped(mock_api, monkeypatch) -> None:
    from briar.auth._acquirers import github_device
    from briar.auth._prompt import MockPromptIO

    monkeypatch.setattr(github_device, "_DEVICE_CODE_URL", mock_api.base_url + "/login/device/code")
    monkeypatch.setenv("BRIAR_GITHUB_CLIENT_ID", "Iv1.placeholder")
    mock_api.add("POST", "/login/device/code", {"message": "server error"}, status=500)

    with pytest.raises(RuntimeError, match="device-code request failed"):
        github_device.GithubDeviceAcquirer().acquire(company="acme", prompt=MockPromptIO())
