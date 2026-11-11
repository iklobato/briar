"""Jira auth strategies, one per `[auth.jira] method` (spec 005).

Same Jira REST API, swappable auth: the Atlassian API token as HTTP Basic
(`token`, the default), or the `Cookie:` header pasted from a logged-in
browser (`cookie`, L9) sent with the headers Atlassian checks on cookie
requests. Each strategy reads its values from the company `JiraLogin`."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, Mapping, Type

from briar.auth.logins import JiraLogin

log = logging.getLogger(__name__)


class JiraAuthStrategy(ABC):
    """Turns a company login into the keyword arguments of `atlassian.Jira(...)`."""

    method: ClassVar[str] = ""

    @abstractmethod
    def configure(self, login: JiraLogin) -> Dict[str, Any]:
        """Kwargs to splat into ``atlassian.Jira(...)``."""


class JiraTokenAuth(JiraAuthStrategy):
    """Email + API token via HTTP Basic. Atlassian-recommended path."""

    method = "token"

    def configure(self, login: JiraLogin) -> Dict[str, Any]:
        return {"username": login.secret("EMAIL"), "password": login.secret("TOKEN")}


class JiraCookieAuth(JiraAuthStrategy):
    """The pasted `Cookie:` header, plus the browser headers Atlassian's edge
    inspects for CSRF (Origin, Referer) and for bots (User-Agent, client hints).
    The set matches a Brave 147 / macOS browser, as 1.x sent it."""

    method = "cookie"

    BROWSER_HEADERS: ClassVar[Mapping[str, str]] = {
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
    XSRF_COOKIE = "atlassian.xsrf.token="

    def configure(self, login: JiraLogin) -> Dict[str, Any]:
        # `requests.Session` is the only path stable across atlassian-python-api 3.41.x
        # (no `header=` kwarg) and 4.x.
        import requests

        origin = login.site_url()
        cookie = login.secret("COOKIE")
        session = requests.Session()
        session.headers.update({**self.BROWSER_HEADERS, "Cookie": cookie, "Origin": origin, "Referer": f"{origin}/", "User-Agent": login.user_agent()})
        if self.XSRF_COOKIE in cookie:
            session.headers["X-Atlassian-Token"] = "no-check"
        return {"session": session}


class JiraAuths:
    """The strategy for the method of a login."""

    STRATEGIES: ClassVar[Mapping[str, Type[JiraAuthStrategy]]] = {strategy.method: strategy for strategy in (JiraTokenAuth, JiraCookieAuth)}

    @classmethod
    def for_login(cls, login: JiraLogin) -> JiraAuthStrategy:
        return cls.STRATEGIES[login.method()]()
