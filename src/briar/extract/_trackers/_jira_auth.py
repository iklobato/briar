"""Jira auth strategies — pluggable how-do-we-talk-to-Jira.

The fundamental shape of the Jira REST API is the same regardless of
how the request is authenticated: same endpoints, same JSON. What
changes is the *authentication envelope* — basic auth vs. browser
session cookies. That's a textbook Strategy: same `JiraTracker`
behaviour, swappable auth.

Two strategies today:

- `JiraTokenAuth` — email + API token via HTTP Basic. The Atlassian-
  recommended path for programmatic access. One token per user; revoke
  via https://id.atlassian.com/manage-profile/security/api-tokens.

- `JiraSessionAuth` — browser-extracted session cookies + browser
  headers. Mimics what a logged-in tab does. Use when:
    * the user can't generate an API token (corporate SSO policy)
    * the tenant rejects token auth (rare, but it happens for certain
      enterprise configurations)
    * you want to act as a specific human's session for short-lived
      operations (debugging, one-off backfills)
  Trade-offs: cookies expire (≈30 days), can be revoked by the user
  logging out, and carry the user's full permissions (not API-token-
  scoped). Atlassian formally deprecated cookie auth for Jira Cloud
  but the browser-session path continues to work in practice.

Selection happens via ``JIRA_{COMPANY}_AUTH_KIND`` (`token` | `session`
| empty for auto-detect). Auto-detect picks `session` when any
session-token env var is set, else `token`. The runbook executor can
also pass an explicit ``auth_kind`` through extractor args (future).

Same Strategy + Registry shape as every other plugin family in the
codebase — TrackerProvider, RepositoryProvider, MessageWriter, etc."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, List, Tuple, Type

from briar._registry import build_registry
from briar.env_vars import CredEnv
from briar.errors import CliError


log = logging.getLogger(__name__)


# Default browser UA — matches the user's pasted request headers (Brave
# on macOS, Chromium 147). Overridable per-company via JIRA_{c}_USER_AGENT.
_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


class JiraAuthStrategy(ABC):
    """Strategy contract — one instance per (company, auth_kind) tuple.

    Each subclass declares its `kind` (registry key), its env-var
    requirements, and how to translate "this company's credentials" into
    the kwargs that `atlassian.Jira(...)` accepts."""

    kind: ClassVar[str] = ""

    @classmethod
    @abstractmethod
    def required_env_vars(cls, *, company: str) -> List[str]:
        """Env-var names the doctor should audit for this strategy."""

    @classmethod
    @abstractmethod
    def is_available(cls, *, company: str) -> bool:
        """True iff every required_env_var for this strategy is set."""

    @abstractmethod
    def configure(self, *, company: str, base_url: str) -> Dict[str, Any]:
        """Return the kwargs to splat into ``atlassian.Jira(...)``.

        Token strategy returns ``{username, password}``; session
        strategy returns ``{cookies, header}``. Cloud + url are passed
        by the tracker, not the strategy."""


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
    """Browser-cookie auth. Reads ``cloud.session.token`` (+ optional
    sibling cookies) and mimics the browser's request envelope so
    Atlassian's edge accepts the request as a logged-in session."""

    kind = "session"

    @classmethod
    def required_env_vars(cls, *, company: str) -> List[str]:
        if not company:
            return []
        # SESSION_TOKEN is the only hard requirement; the other three
        # are optional but listed so `briar secrets doctor` surfaces
        # them as expected configuration knobs.
        return [
            CredEnv.JIRA_SESSION_TOKEN.for_company(company),
        ]

    @classmethod
    def is_available(cls, *, company: str) -> bool:
        if not company:
            return False
        return bool(CredEnv.JIRA_SESSION_TOKEN.read(company))

    def configure(self, *, company: str, base_url: str) -> Dict[str, Any]:
        cloud_session = CredEnv.JIRA_SESSION_TOKEN.read(company)
        if not cloud_session:
            raise CliError(
                f"JiraSessionAuth: missing {CredEnv.JIRA_SESSION_TOKEN.for_company(company)} "
                f"— paste the `cloud.session.token` cookie value from your browser's DevTools"
            )
        tenant_session = CredEnv.JIRA_TENANT_SESSION_TOKEN.read(company)
        xsrf = CredEnv.JIRA_XSRF_TOKEN.read(company)
        user_agent = CredEnv.JIRA_USER_AGENT.read(company) or _DEFAULT_UA

        cookies: Dict[str, str] = {"cloud.session.token": cloud_session}
        if tenant_session:
            cookies["tenant.session.token"] = tenant_session
        if xsrf:
            cookies["atlassian.xsrf.token"] = xsrf

        # Browser-mimicking headers — Atlassian's edge inspects Origin +
        # Referer for CSRF on cookie-authenticated requests, and User-
        # Agent is sniffed by anti-automation layers. The exact set
        # matches the request the user pasted (Brave 147 on macOS).
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
            # Atlassian writes the same xsrf-token value into both the
            # cookie AND a custom header on POSTs; mimicking both keeps
            # write paths working.
            headers["X-Atlassian-Token"] = "no-check"

        return {"cookies": cookies, "header": headers}


_JIRA_AUTHS: Dict[str, Type[JiraAuthStrategy]] = build_registry(
    (JiraTokenAuth, JiraSessionAuth),
    kind="jira auth strategy",
    name_attr="kind",
)


class JiraAuthRegistry:
    """Factory + introspection. Mirrors `TrackerRegistry`."""

    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(_JIRA_AUTHS.keys())

    @classmethod
    def make(cls, kind: str) -> JiraAuthStrategy:
        """Construct an explicit strategy by registered kind."""
        strategy_cls = _JIRA_AUTHS.get(kind)
        if strategy_cls is None:
            known = ", ".join(sorted(_JIRA_AUTHS.keys()))
            raise CliError(f"unknown jira auth strategy {kind!r}; known: {known}")
        return strategy_cls()

    @classmethod
    def autodetect(cls, *, company: str) -> JiraAuthStrategy:
        """Resolve the strategy for a company.

        Priority:
          1. ``JIRA_{COMPANY}_AUTH_KIND`` env var (explicit override)
          2. session auth — picked when any session-token env var is set
          3. token auth — final fallback

        Returns an instance, never None. The instance may not be
        ``is_available`` if no creds are configured for either path —
        callers (typically ``JiraTracker.is_available``) must still
        check before issuing API calls."""
        if company:
            forced = CredEnv.JIRA_AUTH_KIND.read(company)
            if forced:
                return cls.make(forced)
            if JiraSessionAuth.is_available(company=company):
                return JiraSessionAuth()
        return JiraTokenAuth()


__all__ = [
    "JiraAuthStrategy",
    "JiraTokenAuth",
    "JiraSessionAuth",
    "JiraAuthRegistry",
]
