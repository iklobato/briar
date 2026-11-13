"""Slack as the chat source of `briar serve` (spec 008 PR 2, C1, C2, C7, C8, C11), read only,
over the Web API with `httpx`. Search works on Enterprise Grid, where `conversations.history`
and `conversations.list` answer `enterprise_is_restricted` for a user session."""

from __future__ import annotations

import time
from typing import Any, Callable, ClassVar, Dict, FrozenSet, List, Mapping, Optional
from urllib.parse import parse_qs, urlsplit

import httpx

from briar.auth.check import LoginRejected, SourceUnreachable
from briar.auth.logins import SlackLogin
from briar.serve.chat import ChatMessage


class SlackApi:
    """One read call of the Web API. A 429 or a 5xx waits its `Retry-After` (or a short
    backoff) and tries again; a timeout tries again; then the source is unreachable (C8)."""

    BASE = "https://slack.com/api/"
    # Seconds, the same as the other HTTP clients of briar (Jira, the 1.x Slack reader).
    TIMEOUT = 30.0
    ATTEMPTS = 4
    # The longest wait for one retry, in seconds: a poll must not hang for minutes.
    MAX_WAIT = 30.0
    # Without `Retry-After`: 1, 2, 4 seconds.
    BACKOFF_BASE = 2
    RETRY_STATUS: ClassVar[FrozenSet[int]] = frozenset({429, 500, 502, 503, 504})
    READ_METHODS: ClassVar[FrozenSet[str]] = frozenset({"auth.test", "conversations.info", "conversations.replies", "search.messages"})

    def __init__(self, login: SlackLogin, transport: Optional[httpx.BaseTransport], wait: Callable[[float], None] = time.sleep) -> None:
        self._login = login
        self._transport = transport
        self._wait = wait

    def call(self, method: str, params: Mapping[str, Any]) -> Dict[str, Any]:
        if method not in self.READ_METHODS:
            raise LoginRejected(f"briar reads Slack only: {method} is not a read method")
        with httpx.Client(base_url=self.BASE, timeout=self.TIMEOUT, transport=self._transport, headers=self._headers()) as client:
            response = self._send(client, method, {"token": self._login.token(), **params})
        payload: Dict[str, Any] = response.json()
        if not payload.get("ok"):
            raise LoginRejected(f"slack {method}: {payload.get('error', 'not ok')}")
        return payload

    def _send(self, client: httpx.Client, method: str, form: Mapping[str, Any]) -> httpx.Response:
        for attempt in range(self.ATTEMPTS):
            try:
                response = client.post(method, data=dict(form))
            except httpx.TransportError as error:
                self._retry_or_stop(attempt, f"slack {method}: {type(error).__name__}", 0.0)
                continue
            if response.status_code not in self.RETRY_STATUS:
                return response
            self._retry_or_stop(attempt, f"slack {method}: HTTP {response.status_code}", self._delay(response, attempt))
        raise SourceUnreachable(f"slack {method}: no answer after {self.ATTEMPTS} tries")

    def _retry_or_stop(self, attempt: int, what: str, delay: float) -> None:
        if attempt + 1 >= self.ATTEMPTS:
            raise SourceUnreachable(f"{what}, after {self.ATTEMPTS} tries")
        self._wait(delay)

    def _delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after", "")
        return min(float(retry_after) if retry_after.isdigit() else float(self.BACKOFF_BASE**attempt), self.MAX_WAIT)

    def _headers(self) -> Dict[str, str]:
        cookie = self._login.cookie()
        return {"Cookie": f"d={cookie}"} if cookie else {}


class SlackSource:
    """`ChatSource` and `LoginProbe` on Slack."""

    login_source: ClassVar[str] = "slack"
    PAGE = 100
    BOT_PREFIX = "xoxb-"

    def __init__(self, login: SlackLogin, api: SlackApi) -> None:
        self._login = login
        self._api = api

    def who_am_i(self) -> str:
        """C2, C11: the token works, is not a bot token, and is of the workspace of the runbook."""
        if self._login.token().startswith(self.BOT_PREFIX):
            raise LoginRejected("a bot token cannot search Slack; use a user token (xoxp-) or a session token (xoxc-) with its cookie")
        found = self._api.call("auth.test", {})
        host = urlsplit(str(found.get("url") or "")).netloc
        if not host.startswith(f"{self._login.workspace()}."):
            raise LoginRejected(f"the token is of {host or 'another workspace'}, not {self._login.workspace()}")
        return f"{found.get('user')} on {host}"

    def read(self, resource: str) -> None:
        self._api.call("conversations.info", {"channel": resource})

    def owner(self) -> str:
        return str(self._api.call("auth.test", {}).get("user_id") or "")

    def search_all(self, query: str) -> List[ChatMessage]:
        found: List[ChatMessage] = []
        page, pages = 1, 1
        while page <= pages:
            hits = self._search(query, "timestamp", page)
            found += [self._match(match) for match in hits.get("matches") or []]
            page, pages = page + 1, int((hits.get("paging") or {}).get("pages") or 1)
        return found

    def search_top(self, query: str) -> List[ChatMessage]:
        return [self._match(match) for match in self._search(query, "score", 1).get("matches") or []]

    def thread(self, message: ChatMessage) -> List[ChatMessage]:
        replies: List[ChatMessage] = []
        cursor = ""
        while True:
            found = self._api.call("conversations.replies", {"channel": message.channel, "ts": message.thread, "limit": self.PAGE, "cursor": cursor})
            replies += [self._reply(reply, message) for reply in found.get("messages") or []]
            cursor = str((found.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor:
                return replies

    def _search(self, query: str, sort: str, page: int) -> Dict[str, Any]:
        messages: Dict[str, Any] = self._api.call("search.messages", {"query": query, "sort": sort, "count": self.PAGE, "page": page}).get("messages") or {}
        return messages

    def _match(self, match: Mapping[str, Any]) -> ChatMessage:
        channel = match.get("channel") or {}
        thread = parse_qs(urlsplit(str(match.get("permalink") or "")).query).get("thread_ts", [str(match.get("ts") or "")])[0]
        direct = bool(channel.get("is_im") or channel.get("is_mpim")) or str(channel.get("id") or "").startswith("D")
        author = str(match.get("user") or "")
        return ChatMessage(author, str(channel.get("id") or ""), thread, str(match.get("text") or ""), str(match.get("username") or author), self._bot(match), direct)

    def _reply(self, reply: Mapping[str, Any], parent: ChatMessage) -> ChatMessage:
        author = str(reply.get("user") or "")
        return ChatMessage(author, parent.channel, parent.thread, str(reply.get("text") or ""), author, self._bot(reply), parent.direct)

    def _bot(self, message: Mapping[str, Any]) -> bool:
        return bool(message.get("bot_id")) or not message.get("user")


__all__ = ["SlackApi", "SlackSource"]
