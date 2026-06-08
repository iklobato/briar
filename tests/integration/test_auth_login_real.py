"""End-to-end: `briar auth login github-device` driving the REAL acquirer +
REAL envfile credential store + REAL urllib device flow against a wire-level
mock, persisting the token to disk. webbrowser.open is neutralized so the
headless flow never launches a browser; the device flow uses poll (no input()),
so it never blocks.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _wire_device_flow(mock_api, monkeypatch, *, access_token="ACCESS-TOKEN-PLACEHOLDER"):
    import webbrowser

    from briar.auth._acquirers import github_device

    monkeypatch.setattr(github_device, "_DEVICE_CODE_URL", mock_api.base_url + "/login/device/code")
    monkeypatch.setattr(github_device, "_TOKEN_URL", mock_api.base_url + "/login/oauth/access_token")
    monkeypatch.setenv("BRIAR_GITHUB_CLIENT_ID", "Iv1.placeholder")
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: True)  # never launch a real browser
    mock_api.add(
        "POST",
        "/login/device/code",
        {"device_code": "DC", "user_code": "WDJB-MJHT", "verification_uri": "https://github.com/login/device", "expires_in": 900, "interval": 5},
    )
    mock_api.add("POST", "/login/oauth/access_token", {"access_token": access_token, "token_type": "bearer", "scope": "repo,read:org"})


@pytest.mark.timeout(20)
def test_auth_login_github_device_persists_token_to_envfile(cli, mock_api, monkeypatch, tmp_path) -> None:
    secrets_file = tmp_path / "secrets.env"
    monkeypatch.setenv("BRIAR_SECRETS_FILE", str(secrets_file))
    _wire_device_flow(mock_api, monkeypatch)

    result = cli("auth", "login", "github-device", "--company", "acme", "--store", "envfile")

    assert result.code == 0, result.err
    # The token was persisted to the REAL envfile on disk by the real store...
    content = secrets_file.read_text()
    assert "GITHUB_TOKEN=ACCESS-TOKEN-PLACEHOLDER" in content
    # ...and never echoed to stdout.
    assert "ACCESS-TOKEN-PLACEHOLDER" not in result.out
    # The real urllib flow really hit both device endpoints.
    paths = [r["path"] for r in mock_api.received]
    assert "/login/device/code" in paths and "/login/oauth/access_token" in paths


@pytest.mark.timeout(20)
def test_auth_login_missing_client_id_fails_without_persisting(cli, caplog_briar, monkeypatch, tmp_path) -> None:
    secrets_file = tmp_path / "secrets.env"
    monkeypatch.setenv("BRIAR_SECRETS_FILE", str(secrets_file))
    monkeypatch.delenv("BRIAR_GITHUB_CLIENT_ID", raising=False)

    result = cli("auth", "login", "github-device", "--company", "acme", "--store", "envfile")

    # Non-zero exit, the reason is surfaced in the logs, and NOTHING is persisted
    # (a half-written token on a failed login would be a real defect).
    assert result.code != 0
    assert "BRIAR_GITHUB_CLIENT_ID" in caplog_briar.text  # reason surfaced in the logged traceback
    assert not secrets_file.exists() or "GITHUB_TOKEN" not in secrets_file.read_text()
