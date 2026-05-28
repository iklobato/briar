"""Jira auth strategies — Strategy + Registry over the auth envelope.

Same Jira REST API, swappable auth: token (HTTP Basic) or browser
session cookies. Selection via ``JIRA_{COMPANY}_AUTH_KIND``
(``token`` | ``session`` | empty → auto-detect; session wins when
cookies are present, else token)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, List, Tuple, Type

from briar._registry import build_registry
from briar.env_vars import CredEnv
from briar.errors import CliError


log = logging.getLogger(__name__)


# Browser UA — overridable per-company via JIRA_{c}_USER_AGENT.
_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


class JiraAuthStrategy(ABC):
    """One instance per (company, auth_kind). Subclass declares its
    ``kind`` (registry key), env-var requirements, and how to translate
    company creds into ``atlassian.Jira(...)`` kwargs."""

    kind: ClassVar[str] = ""

    @classmethod
    @abstractmethod
    def required_env_vars(cls, *, company: str) -> List[str]:
        """Env-var names the doctor should audit for this strategy."""

    @classmethod
    @abstractmethod
    def is_available(cls, *, company: str) -> bool:
        """True iff every required env var is set."""

    @abstractmethod
    def configure(self, *, company: str, base_url: str) -> Dict[str, Any]:
        """Kwargs to splat into ``atlassian.Jira(...)``."""


class JiraTokenAuth(JiraAuthStrategy):
    """Email + API token via HTTP Basic. Atlassian-recommended path."""

    kind = "token"

    @classmethod
    def required_env_vars(cls, *, company: str) -> List[str]:
        if not company:
            return []
        return [
            CredEnv.JIRA_EMAIL.for_company(company),
            CredEnv.JIRA_TOKEN.for_company(company),
        ]

    @classmethod
    def is_available(cls, *, company: str) -> bool:
        if not company:
            return False
        return bool(CredEnv.JIRA_EMAIL.read(company) and CredEnv.JIRA_TOKEN.read(company))

    def configure(self, *, company: str, base_url: str) -> Dict[str, Any]:
        email = CredEnv.JIRA_EMAIL.read(company)
        token = CredEnv.JIRA_TOKEN.read(company)
        if not (email and token):
            raise CliError(
                f"JiraTokenAuth: missing creds — set "
                f"{CredEnv.JIRA_EMAIL.for_company(company)} + "
                f"{CredEnv.JIRA_TOKEN.for_company(company)}"
            )
        return {"username": email, "password": token}


class JiraSessionAuth(JiraAuthStrategy):
    """Browser-cookie auth. Either ``cloud.session.token`` or
    ``tenant.session.token`` (or both) plus mimicked browser headers."""

    kind = "session"

    @classmethod
    def required_env_vars(cls, *, company: str) -> List[str]:
        if not company:
            return []
        # Either cookie suffices; doctor lists both so the operator
        # knows the choices.
        return [
            CredEnv.JIRA_SESSION_TOKEN.for_company(company),
            CredEnv.JIRA_TENANT_SESSION_TOKEN.for_company(company),
        ]

    @classmethod
    def is_available(cls, *, company: str) -> bool:
        if not company:
            return False
        return bool(
            CredEnv.JIRA_SESSION_TOKEN.read(company)
            or CredEnv.JIRA_TENANT_SESSION_TOKEN.read(company)
        )

    def configure(self, *, company: str, base_url: str) -> Dict[str, Any]:
        cloud_session = CredEnv.JIRA_SESSION_TOKEN.read(company)
        tenant_session = CredEnv.JIRA_TENANT_SESSION_TOKEN.read(company)
        if not (cloud_session or tenant_session):
            raise CliError(
                f"JiraSessionAuth: need at least one of "
                f"{CredEnv.JIRA_SESSION_TOKEN.for_company(company)} (cloud.session.token) or "
                f"{CredEnv.JIRA_TENANT_SESSION_TOKEN.for_company(company)} (tenant.session.token) "
                f"— paste either cookie value from your browser's DevTools"
            )
        xsrf = CredEnv.JIRA_XSRF_TOKEN.read(company)
        user_agent = CredEnv.JIRA_USER_AGENT.read(company) or _DEFAULT_UA

        cookies: Dict[str, str] = {}
        if cloud_session:
            cookies["cloud.session.token"] = cloud_session
        if tenant_session:
            cookies["tenant.session.token"] = tenant_session
        if xsrf:
            cookies["atlassian.xsrf.token"] = xsrf

        # Atlassian's edge inspects Origin + Referer for CSRF on cookie-
        # authenticated requests, and UA is sniffed by anti-automation
        # layers. The exact set matches a Brave 147 / macOS browser.
        origin = base_url.rstrip("/")
        headers = {
            "Origin": origin,
            "Referer": origin + "/",
            "User-Agent": user_agent,
            "Accept": "*/*",
            "Accept-Language": "en;q=0.7",
            "sec-ch-ua": '"Brave";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "sec-gpc": "1",
        }
        if xsrf:
            headers["X-Atlassian-Token"] = "no-check"

        # `requests.Session` is the only path stable across atlassian-
        # python-api 3.41.x (no `header=` kwarg) and 4.x.
        import requests

        session = requests.Session()
        session.cookies.update(cookies)
        session.headers.update(headers)
        return {"session": session}


_JIRA_AUTHS: Dict[str, Type[JiraAuthStrategy]] = build_registry(
    (JiraTokenAuth, JiraSessionAuth),
    kind="jira auth strategy",
    name_attr="kind",
)


def jira_auth_kinds() -> Tuple[str, ...]:
    return tuple(_JIRA_AUTHS.keys())


def make_jira_auth(kind: str) -> JiraAuthStrategy:
    strategy_cls = _JIRA_AUTHS.get(kind)
    if strategy_cls is None:
        known = ", ".join(sorted(_JIRA_AUTHS.keys()))
        raise CliError(f"unknown jira auth strategy {kind!r}; known: {known}")
    return strategy_cls()


def autodetect_jira_auth(*, company: str) -> JiraAuthStrategy:
    """Resolve strategy for a company.

    Priority: explicit ``JIRA_{c}_AUTH_KIND`` → session (if any session
    cookie env var set) → token. Returns an instance, never None;
    callers must still call ``is_available`` before issuing API calls."""
    if company:
        forced = CredEnv.JIRA_AUTH_KIND.read(company)
        if forced:
            return make_jira_auth(forced)
        if JiraSessionAuth.is_available(company=company):
            return JiraSessionAuth()
    return JiraTokenAuth()


__all__ = [
    "JiraAuthStrategy",
    "JiraTokenAuth",
    "JiraSessionAuth",
    "jira_auth_kinds",
    "make_jira_auth",
    "autodetect_jira_auth",
]
