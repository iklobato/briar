"""Central registry for every env var the CLI reads.

One place to look when adding a new credential source. Per-company
entries use `{c}` as a placeholder that `for_company()` substitutes
with the upper-cased, underscore-normalised profile name.

Examples:
    CredEnv.BRIAR_ACCESS.for_company("widget-co")
        -> "BRIAR_WIDGET_CO_ACCESS_TOKEN"

    CredEnv.GITHUB_TOKEN.value
        -> "GITHUB_TOKEN"

Loading priority across the CLI is env -> disk -> in-code default.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Optional


class CredEnv(str, Enum):
    """Env-var names for credentials.

    Members hold the *raw* env var name (or template). Use `.value` for
    workspace-wide vars, `.for_company(name)` for per-company ones."""

    BRIAR_API_BASE     = "BRIAR_API_BASE"
    BRIAR_ACCESS       = "BRIAR_{c}_ACCESS_TOKEN"
    BRIAR_REFRESH      = "BRIAR_{c}_REFRESH_TOKEN"
    BRIAR_WORKSPACE_ID = "BRIAR_{c}_WORKSPACE_ID"

    AWS_KEY_ID  = "AWS_{c}_ACCESS_KEY_ID"
    AWS_SECRET  = "AWS_{c}_SECRET_ACCESS_KEY"
    AWS_SESSION = "AWS_{c}_SESSION_TOKEN"
    AWS_REGION  = "AWS_{c}_REGION"

    GITHUB_TOKEN = "GITHUB_TOKEN"

    JIRA_EMAIL = "JIRA_{c}_EMAIL"
    JIRA_TOKEN = "JIRA_{c}_TOKEN"

    def for_company(self, company: str) -> str:
        normalised = company.upper().replace("-", "_")
        return self.value.format(c=normalised)

    def read(self, company: str = "") -> Optional[str]:
        """Read this env var. `company` is required for templated members,
        ignored for bare ones. Returns None when unset or empty."""
        key = self.for_company(company) if "{c}" in self.value else self.value
        return os.environ.get(key) or None
