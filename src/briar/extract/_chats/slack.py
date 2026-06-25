"""Read-only Slack `ChatProvider`.

Authed with the browser web-session credentials — an ``xoxc-`` token
plus the shared ``d`` (``xoxd-``) cookie — NOT a bot/app token. This is
the same auth the desktop/web client uses: the token alone selects the
workspace, so no per-workspace URL is needed. It mirrors briar's
existing session-auth convention (the ``JIRA_*_SESSION_TOKEN`` family).

Every request goes to ``https://slack.com/api/<method>`` via stdlib
``urllib`` (same dependency-free pattern as `FirefliesMeetingProvider`),
form-encoded with ``token`` in the body and ``Cookie: d=<cookie>`` on
the headers.

READ-ONLY IS ENFORCED, NOT OPTIONAL. Every call funnels through one
``_call`` chokepoint that refuses any method whose terminal verb is not
a read (``list`` / ``info`` / ``history`` / ``replies`` / ``test`` / …)
or a ``search.*`` call, BEFORE the request leaves the machine. This
provider retrieves and searches; it can never post, edit, delete or
react. (Mutation is what `messaging/slack_channel.py` + `notify/slack.py`
are for — those use a webhook, a different credential entirely.)"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from briar._http_retry import urlopen_with_retry
from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.extract._chat import ChatHit, ChatMessage, ChatProvider, ChatThread

log = logging.getLogger(__name__)


_API_BASE = "https://slack.com/api"
_REQUEST_TIMEOUT = 30
# Slack's `search.messages` caps `count` at 100 per request; single-page
# is plenty for the JIT digest sizes briar uses. A thread rarely exceeds
# a few dozen messages, so the same cap bounds `conversations.replies`.
_MAX_COUNT = 100
# A real browser UA — Slack's web API rejects some default clients.
_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"

# A method is read-only iff its terminal verb is one of these OR it is a
# `search.*` call. Mirrors the skill's guard verbatim so the safety
# property is identical: `chat.postMessage`, `*.delete`, `reactions.add`,
# `conversations.mark`, … are all refused before any network call.
_SAFE_TERMINAL_VERBS = frozenset({"test", "list", "info", "history", "replies", "get", "members", "count", "view"})
_READ_ONLY_PREFIXES = ("search.",)


class NotReadOnly(Exception):
    """Raised when a non-read Slack method is requested. The provider is
    read-only by design; this is the last line of defence."""

    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"refusing non-read-only Slack method: {method}")


def _is_read_only(method: str) -> bool:
    if method.startswith(_READ_ONLY_PREFIXES):
        return True
    return method.rsplit(".", 1)[-1] in _SAFE_TERMINAL_VERBS


class SlackChatProvider(ChatProvider):
    kind = "slack"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._token = CredEnv.SLACK_TOKEN.read(company=company) if company else ""
        self._cookie_d = CredEnv.SLACK_COOKIE_D.read(company=company) if company else ""

    def is_available(self) -> bool:
        return bool(self._token and self._cookie_d)

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        if not company:
            return []
        return [
            CredEnv.SLACK_TOKEN.for_company(company),
            CredEnv.SLACK_COOKIE_D.for_company(company),
        ]

    @swallow_errors(default=[], message="slack search_messages")
    def search_messages(self, *, query: str, max_count: int) -> List[ChatHit]:
        if not query.strip():
            return []
        payload = self._call(
            "search.messages",
            {
                "query": query.strip(),
                "count": min(max(max_count, 1), _MAX_COUNT),
                "sort": "score",
                "sort_dir": "desc",
            },
        )
        matches = ((payload.get("messages") or {}).get("matches")) or []
        return [self._to_hit(m) for m in matches if isinstance(m, dict)]

    @swallow_errors(default=None, message="slack get_thread")
    def get_thread(self, *, channel_id: str, thread_ts: str, max_count: int) -> ChatThread:
        payload = self._call(
            "conversations.replies",
            {"channel": channel_id, "ts": thread_ts, "limit": min(max(max_count, 1), _MAX_COUNT)},
        )
        raw = payload.get("messages") or []
        messages = [self._to_message(m) for m in raw if isinstance(m, dict) and (m.get("text") or "").strip()]
        return ChatThread(
            channel_id=channel_id,
            channel_name="",
            root_ts=thread_ts,
            messages=messages,
        )

    # ---- internals --------------------------------------------------------

    def _call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not _is_read_only(method):
            # Belt-and-suspenders: nothing in this class calls a write
            # method, but the guard makes that a structural guarantee
            # rather than a convention a future edit could break.
            raise NotReadOnly(method)
        body = urllib.parse.urlencode({"token": self._token, **{k: str(v) for k, v in params.items()}}).encode("utf-8")
        req = urllib.request.Request(
            f"{_API_BASE}/{method}",
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": f"d={self._cookie_d}",
                "User-Agent": _USER_AGENT,
            },
            method="POST",
        )
        with urlopen_with_retry(req, timeout=_REQUEST_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not payload.get("ok"):
            error = payload.get("error", "unknown")
            # Raise so @swallow_errors surfaces the failure rather than
            # silently returning a half-empty payload (same rationale as
            # FirefliesMeetingProvider._gql). `not_authed`/`invalid_auth`
            # = the session token/cookie expired; name the fix in the
            # message (mirrors the slack skill) so the swallowed log line
            # is actionable, not just `slack api error: invalid_auth`.
            hint = " (session token/cookie likely expired: sign in again and refresh the SLACK_* creds)" if "auth" in error else ""
            raise RuntimeError(f"slack api error: {error}{hint}")
        return payload

    @staticmethod
    def _to_hit(match: Dict[str, Any]) -> ChatHit:
        channel = match.get("channel") or {}
        return ChatHit(
            channel_id=str(channel.get("id") or ""),
            channel_name=str(channel.get("name") or ""),
            ts=str(match.get("ts") or ""),
            text=str(match.get("text") or ""),
            permalink=str(match.get("permalink") or ""),
        )

    @staticmethod
    def _to_message(message: Dict[str, Any]) -> ChatMessage:
        author = str(message.get("user") or "") or str(message.get("username") or "") or str(message.get("bot_id") or "") or "?"
        return ChatMessage(
            ts=str(message.get("ts") or ""),
            author=author,
            text=str(message.get("text") or ""),
        )
