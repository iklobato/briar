"""The chat asks of `briar serve` (spec 008 PR 2, C1, C3, C10, C13, C14). No network: the
chat source is `FakeChat`."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List

from briar.serve.chat import ChatAsks, ChatMessage, ChatReader, Citations, TitleWords
from briar.serve.state import WorkSource
from briar.serve.work import WorkItem, WorkKind
from tests.unit.serve.chat_fakes import OWNER, FakeChat
from tests.unit.serve.fakes import Companies

TODAY = date(2026, 11, 12)
SINCE_DAY = date(2026, 10, 29)
SINCE = SINCE_DAY.isoformat()
ONE_PERSON, CAP = 25, 50
RELATED = 10
MORE_THAN_RELATED = RELATED + 2


class Reader:
    """A `ChatReader` of company acme: Jira project ENG, GitHub repo acme/web, team channel CTEAM01."""

    def __init__(self, tmp_path: Path) -> None:
        self._tmp_path = tmp_path

    def of(self, chat: FakeChat) -> ChatReader:
        extra = {"repos": ["acme/web"], "jira": {"ENG": {"repos": ["acme/app"]}}, "chat": {"slack": {"channels": ["CTEAM01"]}}}
        return ChatReader(chat, Companies(self._tmp_path).company(extra).settings, lambda: TODAY)


class Says:
    """Chat messages and tickets of the tests."""

    def said(self, author: str, text: str, channel: str = "CTEAM01") -> ChatMessage:
        return ChatMessage(author, channel, f"{author}.{len(text)}", text, author)

    def ticket(self, key: str, title: str) -> WorkItem:
        return WorkItem(WorkKind.TICKET, key, WorkSource.JIRA, "acme/app", title)


class TestCitations:
    def test_jira_and_github_keys_and_links_count_and_a_bare_number_does_not(self) -> None:
        citations = Citations(["ENG"], ["acme/web"])
        text = "see ENG-12, https://acme.atlassian.net/browse/ENG-13, acme/web#4, https://github.com/acme/web/issues/5 and #6; not ENGX-1 or other/acme/web#7"

        assert citations.keys(text) == {"ENG-12", "ENG-13", "acme/web#4", "acme/web#5"}

    def test_one_quoted_search_per_project_and_repo(self) -> None:
        assert Citations(["ENG"], ["acme/web"]).queries(SINCE_DAY) == [f'"ENG-" after:{SINCE}', f'"acme/web" after:{SINCE}']


class TestAsks:
    def test_distinct_people_count_once_and_the_owner_and_bots_not_at_all(self, tmp_path: Path) -> None:
        says = Says()
        bot = ChatMessage("B1", "CTEAM01", "9.0", "ENG-2 moved to In Review", "jira", bot=True)
        found = [says.said("U2", "ENG-1 please"), says.said("U2", "ENG-1 again?"), says.said("U3", "ENG-1 +1"), says.said("U4", "and ENG-1"), says.said(OWNER, "ENG-2 me"), bot]

        asks = Reader(tmp_path).of(FakeChat(found)).asks()

        assert (asks.points("ENG-1"), asks.points("ENG-2"), asks.points("ENG-3")) == (CAP, 0, 0)

    def test_a_message_found_by_two_searches_is_kept_once(self, tmp_path: Path) -> None:
        """Each search of `FakeChat` gives every message, as two real searches give one that cites a key and a repo."""
        message = Says().said("U2", "ENG-1 is about acme/web#4")

        asks = Reader(tmp_path).of(FakeChat([message])).asks()

        assert asks.messages == {"ENG-1": (message,), "acme/web#4": (message,)}

    def test_one_person_gives_25(self) -> None:
        assert ChatAsks({"ENG-1": frozenset({"U2"})}).points("ENG-1") == ONE_PERSON

    def test_the_searches_cover_14_days(self, tmp_path: Path) -> None:
        chat = FakeChat([])

        Reader(tmp_path).of(chat).asks()

        assert chat.queries == [f'"ENG-" after:{SINCE}', f'"acme/web" after:{SINCE}']


class TestTitleWords:
    def test_short_and_common_words_are_dropped(self) -> None:
        assert TitleWords("Fix the Lambda node is down after deploy").query(SINCE_DAY) == f"Lambda node down deploy after:{SINCE}"

    def test_a_title_with_no_strong_word_gives_no_search(self) -> None:
        assert TitleWords("Fix it").query(SINCE_DAY) == ""


class TestTalk:
    def test_a_related_thread_counts_only_in_a_dm_a_mention_or_a_team_channel(self, tmp_path: Path) -> None:
        says = Says()
        dm = ChatMessage("U2", "D1", "1.0", "the lambda node keeps dying", "ana", direct=True)
        mention = says.said("U3", f"<@{OWNER}> lambda node down again", "COTHER1")
        team = says.said("U4", "lambda node is flapping")
        elsewhere = says.said("U5", "lambda node in my side project", "COTHER1")
        chat = FakeChat([], related=[dm, mention, team, elsewhere])

        talk = Reader(tmp_path).of(chat).talk([says.ticket("ENG-1", "Lambda node is down")], ChatAsks())

        assert talk == {"ENG-1": [dm.line(), mention.line(), team.line()]}

    def test_a_key_citation_and_a_related_hit_of_the_same_thread_give_the_thread_once(self, tmp_path: Path) -> None:
        says = Says()
        root = says.said("U2", "ENG-1 needs an owner")
        reply = ChatMessage("U3", "CTEAM01", root.thread, "lambda node is down here too", "bo")
        chat = FakeChat([], related=[reply], threads={root.thread: [root, reply]})
        asks = ChatAsks({"ENG-1": frozenset({"U2"})}, {"ENG-1": (root,)})

        talk = Reader(tmp_path).of(chat).talk([says.ticket("ENG-1", "Lambda node is down")], asks)

        assert talk == {"ENG-1": [f"{root.line()}\n{reply.line()}"]}

    def test_at_most_10_related_hits_per_ticket(self, tmp_path: Path) -> None:
        says = Says()
        hits: List[ChatMessage] = [says.said(f"U{index}", f"lambda node {index}") for index in range(MORE_THAN_RELATED)]

        talk = Reader(tmp_path).of(FakeChat([], related=hits)).talk([says.ticket("ENG-1", "Lambda node is down")], ChatAsks())

        assert len(talk["ENG-1"]) == RELATED
