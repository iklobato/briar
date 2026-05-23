"""GitHub OAuth device flow acquirer.

Implements the device-flow grant against GitHub's OAuth endpoints
(``github.com/login/device/code`` + ``github.com/login/oauth/access_token``).
The user visits the verification URL, enters a short code, and the
CLI polls until authorisation completes. The CLI never sees the
password — same security shape as ``gh auth login``.

Requires an OAuth App's client_id. Env var: ``BRIAR_GITHUB_CLIENT_ID``
(global; one OAuth app per briar install). To create one:
https://github.com/settings/applications/new — set "Device flow"
to enabled, callback URL can be anything (unused for device flow).

Stores ``GITHUB_TOKEN``."""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from typing import List

from briar.auth._acquirer import CredentialAcquirer, Credentials
from briar.auth._prompt import PromptIO


log = logging.getLogger(__name__)

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_DEFAULT_SCOPES = "repo,read:org"


def _post_form(url: str, fields: dict) -> dict:
    """POST application/x-www-form-urlencoded with Accept: application/json.
    Returns parsed JSON. Raises on non-2xx."""
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "User-Agent": "briar-cli"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


class GithubDeviceAcquirer(CredentialAcquirer):
    kind = "github-device"
    display_name = "GitHub OAuth device flow"

    def acquire(self, *, company: str, prompt: PromptIO) -> Credentials:
        client_id = os.environ.get("BRIAR_GITHUB_CLIENT_ID", "").strip()
        if not client_id:
            raise RuntimeError(
                "github-device: BRIAR_GITHUB_CLIENT_ID env var required. "
                "Register an OAuth App at https://github.com/settings/applications/new "
                "with 'Device flow' enabled, then export the client_id."
            )

        # Step 1: request a device code.
        try:
            init = _post_form(
                _DEVICE_CODE_URL,
                {"client_id": client_id, "scope": _DEFAULT_SCOPES},
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"github-device: device-code request failed: {exc}")

        device_code = init.get("device_code") or ""
        user_code = init.get("user_code") or ""
        verification_uri = init.get("verification_uri") or "https://github.com/login/device"
        interval = max(int(init.get("interval", 5)), 1)
        expires_in = int(init.get("expires_in", 900))

        if not (device_code and user_code):
            raise RuntimeError(f"github-device: malformed device-code response: {init}")

        # Step 2: prompt user.
        prompt.info("==> GitHub OAuth — device flow")
        prompt.info(f"    1. Open {verification_uri}")
        prompt.info(f"    2. Enter code: {user_code}")
        prompt.info(f"    3. Authorise the app (scopes: {_DEFAULT_SCOPES})")
        prompt.info(f"    polling every {interval}s for up to {expires_in}s …")
        prompt.open_url(verification_uri)

        # Step 3: poll the token endpoint.
        def _poll_once():
            try:
                resp = _post_form(
                    _TOKEN_URL,
                    {
                        "client_id": client_id,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("github-device: poll error (will retry): %s", exc)
                return None
            err = resp.get("error", "")
            # `authorization_pending` is the normal "user hasn't acted yet"
            # response — keep polling. `slow_down` means double the interval
            # (rare; servers rarely send it). Other errors abort.
            if err == "authorization_pending":
                return None
            if err == "slow_down":
                return None
            if err:
                raise RuntimeError(f"github-device: OAuth error: {err} — {resp.get('error_description', '')}")
            token = resp.get("access_token", "")
            return token or None

        try:
            token = prompt.poll(every=interval, max_wait=expires_in, fn=_poll_once)
        except TimeoutError:
            raise RuntimeError("github-device: timed out waiting for authorisation")

        return Credentials(
            provider_kind=self.kind,
            entries={"GITHUB_TOKEN": str(token)},
            metadata={"scopes": _DEFAULT_SCOPES, "auth_mode": "oauth-device"},
        )

    @classmethod
    def writes(cls, *, company: str) -> List[str]:
        return ["GITHUB_TOKEN"]
