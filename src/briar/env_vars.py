"""Central registry for env vars the CLI reads.

`{c}` in a template is the per-company prefix that `for_company()`
substitutes with the upper-cased, underscore-normalised name.

Examples:
    CredEnv.AWS_KEY_ID.for_company("widget-co")
        -> "AWS_WIDGET_CO_ACCESS_KEY_ID"

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

    JIRA_EMAIL = "JIRA_{c}_EMAIL"
    JIRA_TOKEN = "JIRA_{c}_TOKEN"

    def for_company(self, company: str) -> str:
        normalised = company.upper().replace("-", "_")
        return self.value.format(c=normalised)

    def read(self, company: str = "") -> str:
        """Return the env-var value, or `""` when unset. Callers should
        check truthiness (`if env.read("foo"):`) rather than identity."""
        key = self.for_company(company) if "{c}" in self.value else self.value
        return os.environ.get(key, "")
