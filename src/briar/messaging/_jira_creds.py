"""Shared Jira credential resolution for messaging writers.

Both `JiraCommentWriter` and `JiraTransitionWriter` read the same
per-company URL + email + token env vars and then construct the
exact same `atlassian.Jira(...)` client. The credential read was
copy-pasted across the two writers; with more Jira-flavoured writers
on the way (jira-attach, jira-link, …) the drift hazard was real."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from briar.env_vars import CredEnv


@dataclass(frozen=True)
class JiraCreds:
    """Per-company Jira URL + email + API token, read from the
    canonical ``JIRA_<COMPANY>_*`` env vars."""

    url: str
    email: str
    token: str

    @classmethod
    def from_env(cls, company: str) -> "JiraCreds":
        if not company:
            return cls(url="", email="", token="")
        return cls(
            url=CredEnv.JIRA_URL.read(company=company),
            email=CredEnv.JIRA_EMAIL.read(company=company),
            token=CredEnv.JIRA_TOKEN.read(company=company),
        )

    def is_complete(self) -> bool:
        return bool(self.url and self.email and self.token)

    @staticmethod
    def required_env_vars(company: str) -> List[str]:
        """Env var names every Jira writer needs the doctor to report."""
        if not company:
            return []
        return [
            CredEnv.JIRA_URL.for_company(company),
            CredEnv.JIRA_EMAIL.for_company(company),
            CredEnv.JIRA_TOKEN.for_company(company),
        ]
