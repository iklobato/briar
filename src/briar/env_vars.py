"""Central registry for env vars the CLI reads.

`{c}` in a template is the per-company prefix that `for_company()`
substitutes with the upper-cased, underscore-normalised name.

Examples:
    CredEnv.AWS_KEY_ID.for_company("acme-co")
        -> "AWS_ACME_CO_ACCESS_KEY_ID"

    CredEnv.GITHUB_TOKEN.value
        -> "GITHUB_TOKEN"
"""

from __future__ import annotations

import os
from enum import Enum


class CredEnv(str, Enum):
    AWS_KEY_ID = "AWS_{c}_ACCESS_KEY_ID"
    AWS_SECRET = "AWS_{c}_SECRET_ACCESS_KEY"
    AWS_SESSION = "AWS_{c}_SESSION_TOKEN"
    AWS_REGION = "AWS_{c}_REGION"

    GITHUB_TOKEN = "GITHUB_TOKEN"

    # Bitbucket Cloud uses basic auth: a user identity plus an app password.
    # Per-company (workspace-scoped) because Bitbucket app passwords are
    # tied to a specific user/workspace membership — unlike GitHub PATs,
    # they do not span orgs.
    BITBUCKET_USERNAME = "BITBUCKET_{c}_USERNAME"
    BITBUCKET_APP_PASSWORD = "BITBUCKET_{c}_APP_PASSWORD"
    BITBUCKET_WORKSPACE = "BITBUCKET_{c}_WORKSPACE"

    JIRA_EMAIL = "JIRA_{c}_EMAIL"
    JIRA_TOKEN = "JIRA_{c}_TOKEN"
    JIRA_URL = "JIRA_{c}_URL"
    # Optional per-company override: `token` | `session` | `` (auto).
    # When unset, autodetect_jira_auth picks `session` if any
    # session-token env var is present, else `token`.
    JIRA_AUTH_KIND = "JIRA_{c}_AUTH_KIND"
    # Session-auth (browser-cookie) credentials — paste the values
    # from your browser's DevTools while logged into the Jira tenant:
    JIRA_SESSION_TOKEN = "JIRA_{c}_SESSION_TOKEN"  # `cloud.session.token`
    JIRA_TENANT_SESSION_TOKEN = "JIRA_{c}_TENANT_SESSION_TOKEN"  # newer `tenant.session.token` (optional)
    JIRA_XSRF_TOKEN = "JIRA_{c}_XSRF_TOKEN"  # `atlassian.xsrf.token` (required for POSTs)
    JIRA_USER_AGENT = "JIRA_{c}_USER_AGENT"  # optional UA override

    LINEAR_TOKEN = "LINEAR_{c}_TOKEN"

    # Fireflies.ai personal API key. Per-company because workspaces are
    # billed separately and each owns its own transcript corpus.
    FIREFLIES_API_KEY = "FIREFLIES_{c}_API_KEY"

    def for_company(self, company: str) -> str:
        if "{c}" in self.value and not company:
            # Empty company on a templated var used to silently produce
            # the double-underscore "AWS__ACCESS_KEY_ID" form which
            # never matched any operator-set env. Refuse so callers see
            # the misuse immediately.
            raise ValueError(f"CredEnv.{self.name} requires a non-empty company (template={self.value!r})")
        normalised = company.upper().replace("-", "_")
        return self.value.format(c=normalised)

    def read(self, company: str = "") -> str:
        """Return the env-var value, or `""` when unset. Callers should
        check truthiness (`if env.read("foo"):`) rather than identity."""
        if "{c}" in self.value:
            if not company:
                return ""  # templated var without company → not configured
            key = self.for_company(company)
        else:
            key = self.value
        return os.environ.get(key, "")
