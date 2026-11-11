"""Central registry for env vars the CLI reads.

`{c}` in a template is the per-company prefix that `for_company()`
substitutes with the upper-cased, underscore-normalised name.

Example:
    CredEnv.AWS_KEY_ID.for_company("acme-co")
        -> "AWS_ACME_CO_ACCESS_KEY_ID"
"""

from __future__ import annotations

import os
from enum import Enum


class CredEnv(str, Enum):
    AWS_KEY_ID = "AWS_{c}_ACCESS_KEY_ID"
    AWS_SECRET = "AWS_{c}_SECRET_ACCESS_KEY"
    AWS_SESSION = "AWS_{c}_SESSION_TOKEN"
    AWS_REGION = "AWS_{c}_REGION"

    # The other 1.x names left with spec 005 PR 1: the logins of `briar.auth.logins`
    # use `BRIAR_<COMPANY>_<SOURCE>_<FIELD>` (G7). AWS moves in PR 2.

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
