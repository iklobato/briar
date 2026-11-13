"""Slack as the chat source of `briar serve` (spec 008 PR 2, C2, C7, C8, C11). No network: an
`httpx.MockTransport` answers as the Slack Web API does."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List
from urllib.parse import parse_qs

import httpx
import pytest

from briar.auth.check import LoginRejected, SourceUnreachable
from briar.auth.config import AuthBlock, SlackAuth, SlackMethod
from briar.auth.logins import CompanyLogins, LoginScope, SlackLogin
from briar.serve.chat import ChatMessage
from briar.serve.slack import SlackApi, SlackSource

FAKE_TOKEN = "xoxc-PLACEHOLDER-not-a-secret"
FAKE_COOKIE = "PLACEHOLDER-cookie"
RETRY_AFTER = 7
TOO_MANY = 429
TWO_PAGES = 2


@dataclass(frozen=True)
class Slack:
    """The fake Web API: `answers` gives the JSON of each call in turn; records each request."""

    answers: List[Callable[[httpx.Request], httpx.Response]]
    requests: List[httpx.Request] = field(default_factory=list)
    waits: List[float] = field(default_factory=list)

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answers[len(self.requests) - 1](request)

    def form(self, index: int) -> Dict[str, str]:
        return {key: values[0] for key, values in parse_qs(self.requests[index].content.decode()).items()}

    def api(self, token: str = FAKE_TOKEN) -> SlackApi:
        return SlackApi(Logins().slack(token), httpx.MockTransport(self.answer), self.waits.append)

    def source(self, token: str = FAKE_TOKEN) -> SlackSource:
        return SlackSource(Logins().slack(token), self.api(token))


class Logins:
    def slack(self, token: str) -> SlackLogin:
        block = AuthBlock(slack=SlackAuth(method=SlackMethod.SESSION, workspace="acme"))
        environ = {"BRIAR_ACME_SLACK_TOKEN": token, "BRIAR_ACME_SLACK_COOKIE": FAKE_COOKIE}
        return CompanyLogins(LoginScope("acme"), block, environ).slack()


class Answer:
    """One answer of the fake Web API."""

    def ok(self, body: Dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
        return lambda request: httpx.Response(200, json={"ok": True, **body})

    def error(self, code: str) -> Callable[[httpx.Request], httpx.Response]:
        return lambda request: httpx.Response(200, json={"ok": False, "error": code})

    def limited(self) -> Callable[[httpx.Request], httpx.Response]:
        return lambda request: httpx.Response(TOO_MANY, headers={"Retry-After": str(RETRY_AFTER)})

    def timeout(self) -> Callable[[httpx.Request], httpx.Response]:
        return self._raise_timeout

    def _raise_timeout(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)


class TestApi:
    def test_the_token_goes_in_the_form_and_the_cookie_in_its_header(self) -> None:
        slack = Slack([Answer().ok({"user": "ana"})])

        assert slack.api().call("auth.test", {})["user"] == "ana"
        assert slack.form(0)["token"] == FAKE_TOKEN and slack.requests[0].headers["Cookie"] == f"d={FAKE_COOKIE}"

    def test_a_429_waits_its_retry_after_then_tries_again(self) -> None:
        slack = Slack([Answer().limited(), Answer().ok({"user": "ana"})])

        slack.api().call("auth.test", {})

        assert slack.waits == [RETRY_AFTER]

    def test_a_source_that_keeps_timing_out_is_unreachable(self) -> None:
        slack = Slack([Answer().timeout()] * SlackApi.ATTEMPTS)

        with pytest.raises(SourceUnreachable, match=f"slack auth.test: ReadTimeout, after {SlackApi.ATTEMPTS} tries"):
            slack.api().call("auth.test", {})
        assert len(slack.waits) == SlackApi.ATTEMPTS - 1

    def test_an_error_answer_is_refused_with_its_code(self) -> None:
        with pytest.raises(LoginRejected, match="slack auth.test: invalid_auth"):
            Slack([Answer().error("invalid_auth")]).api().call("auth.test", {})

    def test_a_write_method_is_refused_before_any_request(self) -> None:
        slack = Slack([])

        with pytest.raises(LoginRejected, match="briar reads Slack only: chat.postMessage is not a read method"):
            slack.api().call("chat.postMessage", {"text": "hi"})
        assert slack.requests == []


class TestWhoAmI:
    def test_a_bot_token_is_refused_before_any_request(self) -> None:
        slack = Slack([])

        with pytest.raises(LoginRejected, match="a bot token cannot search Slack"):
            slack.source("xoxb-PLACEHOLDER").who_am_i()
        assert slack.requests == []

    def test_a_token_of_another_workspace_is_refused(self) -> None:
        with pytest.raises(LoginRejected, match="the token is of other.slack.com, not acme"):
            Slack([Answer().ok({"url": "https://other.slack.com/", "user": "ana"})]).source().who_am_i()

    def test_the_account_and_the_workspace(self) -> None:
        assert Slack([Answer().ok({"url": "https://acme.slack.com/", "user": "ana"})]).source().who_am_i() == "ana on acme.slack.com"


class TestRead:
    def test_every_page_of_a_search_is_read(self) -> None:
        reply = {"user": "U2", "username": "ana", "text": "ENG-1?", "channel": {"id": "C1"}, "ts": "2.0", "permalink": "https://acme.slack.com/archives/C1/p2?thread_ts=1.0"}
        dm = {"user": "U3", "text": "ENG-2", "channel": {"id": "D9", "is_im": True}, "ts": "3.0", "permalink": "https://acme.slack.com/archives/D9/p3"}
        bot = {"bot_id": "B1", "username": "jira", "text": "ENG-3 moved", "channel": {"id": "C1"}, "ts": "4.0"}
        paging = {"pages": TWO_PAGES}
        pages = [Answer().ok({"messages": {"matches": [reply, dm], "paging": paging}}), Answer().ok({"messages": {"matches": [bot], "paging": paging}})]
        slack = Slack(pages)

        found = slack.source().search_all('"ENG-" after:2026-10-29')

        assert found == [ChatMessage("U2", "C1", "1.0", "ENG-1?", "ana"), ChatMessage("U3", "D9", "3.0", "ENG-2", "U3", direct=True), ChatMessage("", "C1", "4.0", "ENG-3 moved", "jira", bot=True)]
        assert [slack.form(index)["page"] for index in range(len(pages))] == ["1", "2"]

    def test_a_thread_follows_its_cursor(self) -> None:
        parent = ChatMessage("U2", "C1", "1.0", "ENG-1?", "ana")
        pages = [Answer().ok({"messages": [{"user": "U2", "text": "ENG-1?"}], "response_metadata": {"next_cursor": "next"}}), Answer().ok({"messages": [{"user": "U3", "text": "on it"}]})]
        slack = Slack(pages)

        replies = slack.source().thread(parent)

        assert [reply.text for reply in replies] == ["ENG-1?", "on it"] and slack.form(1)["cursor"] == "next"
