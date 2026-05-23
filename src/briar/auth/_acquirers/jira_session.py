"""Jira browser-session-cookie acquirer.

Walks the user through DevTools cookie extraction. For tenants where
the user cannot generate an API token (SSO policy, restricted
account types).

Stores ``JIRA_<COMPANY>_URL``, ``JIRA_<COMPANY>_TENANT_SESSION_TOKEN``,
and sets ``JIRA_<COMPANY>_AUTH_KIND=session``. ``cloud.session.token``
+ ``atlassian.xsrf.token`` are accepted as optional extras.

Parses the JWT payload to record ``expires_at`` so the CLI can warn
about the upcoming rotation."""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from briar.auth._acquirer import CredentialAcquirer, Credentials
from briar.auth._prompt import PromptIO
from briar.env_vars import CredEnv


log = logging.getLogger(__name__)


def _decode_jwt_exp(jwt: str) -> Optional[datetime]:
    """Return the ``exp`` claim as a tz-aware UTC datetime, or None
    if the token isn't a 3-segment JWT or doesn't carry an exp.
    Silently tolerates malformed segments — the cookie is the source
    of truth; expiry detection is a nice-to-have."""
    parts = jwt.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:  # noqa: BLE001
        return None
    exp = claims.get("exp")
    if not isinstance(exp, int):
        return None
    return datetime.fromtimestamp(exp, tz=timezone.utc)


class JiraSessionAcquirer(CredentialAcquirer):
    kind = "jira-session"
    display_name = "Jira browser session cookie (DevTools paste)"

    def acquire(self, *, company: str, prompt: PromptIO) -> Credentials:
        if not company:
            raise ValueError("jira-session: --company is required")

        prompt.info("==> Jira browser-session cookie")
        prompt.info("    1. Log into your Jira tenant in the browser (https://<org>.atlassian.net)")
        prompt.info("    2. DevTools → Application → Cookies → <your tenant>")
        prompt.info("    3. Click the `tenant.session.token` row")
        prompt.info("    4. DOUBLE-CLICK the Value cell and copy the full value (starts with `eyJ`)")
        prompt.info("       (drag-selecting often truncates the start — use double-click)")

        url = prompt.prompt("    Jira URL (https://<org>.atlassian.net): ").strip().rstrip("/")
        tenant_token = prompt.prompt("    paste tenant.session.token: ", secret=True).strip()
        cloud_token = prompt.prompt("    paste cloud.session.token (optional, ENTER to skip): ", secret=True).strip()
        xsrf_token = prompt.prompt("    paste atlassian.xsrf.token (optional, ENTER to skip): ", secret=True).strip()

        if not (url and tenant_token):
            raise ValueError("jira-session: URL + tenant.session.token required (at minimum)")

        entries = {
            CredEnv.JIRA_URL.for_company(company): url,
            CredEnv.JIRA_TENANT_SESSION_TOKEN.for_company(company): tenant_token,
            CredEnv.JIRA_AUTH_KIND.for_company(company): "session",
        }
        if cloud_token:
            entries[CredEnv.JIRA_SESSION_TOKEN.for_company(company)] = cloud_token
        if xsrf_token:
            entries[CredEnv.JIRA_XSRF_TOKEN.for_company(company)] = xsrf_token

        expires_at = _decode_jwt_exp(tenant_token)
        return Credentials(
            provider_kind=self.kind,
            entries=entries,
            expires_at=expires_at,
            metadata={"auth_mode": "browser-session"},
        )

    @classmethod
    def writes(cls, *, company: str) -> List[str]:
        if not company:
            return []
        # The mandatory writes; cloud/xsrf are optional and not listed
        # (doctor reports them as nice-to-have via JiraSessionAuth's
        # required_env_vars).
        return [
            CredEnv.JIRA_URL.for_company(company),
            CredEnv.JIRA_TENANT_SESSION_TOKEN.for_company(company),
            CredEnv.JIRA_AUTH_KIND.for_company(company),
        ]
