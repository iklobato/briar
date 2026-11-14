"""How `briar serve` chooses its next tickets (spec 008 PR 1: N2 to N7, N9 to N11, Q1 to Q3):
the tickets nobody can work now are dropped, the rest are scored, the model picks among the
5 best. No network: the agent is scripted."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pytest

from briar.auth.check import LoginRejected
from briar.extract._tracker import Ticket
from briar.iac.runbook.models import ServeSettings
from briar.serve.agent import Access, AgentAnswer, AgentRequest
from briar.serve.chat import ChatMessage, ChatReader
from briar.serve.select import Choice, Score, Selector, Shortlist, Workable
from briar.serve.state import WorkSource
from briar.serve.work import ServeCompany, WorkItem, WorkKind
from tests.unit.serve.chat_fakes import FakeChat
from tests.unit.serve.fakes import Companies

TODAY = date(2026, 11, 12)
SELECTION_COST = 0.04
TOP = 5
SLOTS = 2
MANY = 7
UNBLOCKS_THREE = 3
UNBLOCKS_FIVE = 5
ASKED_TWICE = 2
# The points of decision Q1, one signal at a time.
OVERDUE, WITHIN_WEEK, WITHIN_MONTH, LATER = 50, 40, 20, 5
UNBLOCK_CAP, HIGHEST, LOW, LABEL, STARTED, EPIC = 30, 20, -5, 10, 30, 0


class Tickets:
    """Jira tickets of project ENG with the signals of the selector."""

    BASE = Ticket(key="", title="", reporter="", assignee="", status="To Do", kind="Task", priority="Medium", created_at="2026-01-01")

    def item(self, key: str, signals: Dict[str, Any]) -> WorkItem:
        ticket = replace(self.BASE, key=key, title=f"title of {key}", **signals)
        return WorkItem(WorkKind.TICKET, key, WorkSource.JIRA, "", ticket.title, created_at=ticket.created_at, repos=("acme/app", "acme/api"), ticket=ticket)

    def plain(self, key: str) -> WorkItem:
        return self.item(key, {})


@dataclass(frozen=True)
class PickingAgent:
    """The selector model: answers `picks` and records what it was asked."""

    picks: List[Dict[str, str]]
    requests: List[AgentRequest] = field(default_factory=list)

    async def ask(self, request: AgentRequest) -> AgentAnswer:
        self.requests.append(request)
        return AgentAnswer("", {"picks": self.picks}, SELECTION_COST)


class Setup:
    """A company with Jira project ENG and two allowed repos."""

    def __init__(self, tmp_path: Path) -> None:
        self._tmp_path = tmp_path

    def company(self) -> ServeCompany:
        return Companies(self._tmp_path).company({"jira": {"ENG": {"repos": ["acme/app", "acme/api"]}}})

    def settings(self) -> ServeSettings:
        return self.company().settings

    def selector(self, agent: PickingAgent) -> Selector:
        return Selector(agent, self.company())


class TestWorkable:
    def test_a_not_workable_status_and_an_open_blocker_are_dropped(self, tmp_path: Path) -> None:
        tickets = Tickets()
        items = [tickets.plain("ENG-1"), tickets.item("ENG-2", {"status": "In Review"}), tickets.item("ENG-3", {"open_blockers": 1}), tickets.item("ENG-4", {"status": "Blocked"})]

        kept = Workable(Setup(tmp_path).settings()).keep(items)

        assert [item.key for item in kept] == ["ENG-1"]

    def test_only_keeps_the_listed_tickets(self, tmp_path: Path) -> None:
        """Validation plan S1: a first run on a real tracker touches the listed tickets alone."""
        settings = Companies(tmp_path).company({"jira": {"ENG": {"repos": ["acme/app"]}}, "only": ["ENG-2"]}).settings
        tickets = Tickets()

        kept = Workable(settings).keep([tickets.plain("ENG-1"), tickets.plain("ENG-2")])

        assert [item.key for item in kept] == ["ENG-2"]


class TestScore:
    def test_due_soon_unblocking_and_in_progress_rank_before_a_plain_ticket(self) -> None:
        tickets = Tickets()
        plain = tickets.item("ENG-1", {"created_at": "2025-01-01"})
        due = tickets.item("ENG-2", {"due_date": "2026-11-18"})
        unblocking = tickets.item("ENG-3", {"unblocks": UNBLOCKS_THREE})
        started = tickets.item("ENG-4", {"in_progress": True})

        ranked = Score(TODAY, []).rank([plain, due, unblocking, started])

        assert [item.key for item in ranked] == ["ENG-2", "ENG-3", "ENG-4", "ENG-1"]

    def test_the_weights_of_q1(self) -> None:
        score, tickets = Score(TODAY, ["p0"]), Tickets()
        cases = [
            ({"due_date": "2026-11-01"}, OVERDUE),
            ({"due_date": "2026-11-19"}, WITHIN_WEEK),
            ({"due_date": "2026-12-12"}, WITHIN_MONTH),
            ({"due_date": "2027-06-01"}, LATER),
            ({"unblocks": UNBLOCKS_FIVE}, UNBLOCK_CAP),
            ({"priority": "Highest"}, HIGHEST),
            ({"priority": "Low"}, LOW),
            ({"labels": ["p0"]}, LABEL),
            ({"in_progress": True}, STARTED),
            ({"epic": "ENG-100"}, EPIC),
        ]

        assert [score.of(tickets.item("ENG-1", signals).ticket) for signals, _ in cases] == [points for _, points in cases]

    def test_ties_keep_the_oldest_first(self) -> None:
        tickets = Tickets()

        ranked = Score(TODAY, []).rank([tickets.item("ENG-2", {"created_at": "2026-02-01"}), tickets.item("ENG-1", {"created_at": "2026-01-01"})])

        assert [item.key for item in ranked] == ["ENG-1", "ENG-2"]


class TestSelector:
    def test_the_model_gets_the_5_best_and_its_picks_become_items_with_repo_reason_and_cost(self, tmp_path: Path) -> None:
        tickets = Tickets()
        agent = PickingAgent([{"key": "ENG-3", "repo": "acme/api", "reason": "unblocks the release"}])

        picked = asyncio.run(Setup(tmp_path).selector(agent).pick([tickets.plain(f"ENG-{index}") for index in range(1, MANY + 1)], SLOTS, {}))

        assert len(agent.requests) == 1 and agent.requests[0].prompt.count("\n- ENG-") == TOP
        # The pick needs no tool: a call that may read paid for every tool of the CLI.
        assert agent.requests[0].access is Access.ANSWER
        assert [(item.key, item.repo, item.reason, item.selection_cost) for item in picked] == [("ENG-3", "acme/api", "unblocks the release", SELECTION_COST)]

    def test_a_pick_outside_the_allowed_repos_or_the_shortlist_is_skipped(self, tmp_path: Path) -> None:
        tickets = Tickets()
        picks = [{"key": "ENG-1", "repo": "evil/repo", "reason": "x"}, {"key": "ENG-9", "repo": "acme/app", "reason": "y"}, {"key": "ENG-2", "repo": "none", "reason": "no repo fits"}]

        picked = asyncio.run(Setup(tmp_path).selector(PickingAgent(picks)).pick([tickets.plain("ENG-1"), tickets.plain("ENG-2")], SLOTS, {}))

        assert picked == []

    def test_a_skipped_pick_is_logged_with_why(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """The first real run: the model answered `1` for `acme/app#1` and both picks were dropped with no line."""
        picks = [{"key": "1", "repo": "acme/app", "reason": "x"}, {"key": "ENG-1", "repo": "evil/repo", "reason": "y"}]

        asyncio.run(Setup(tmp_path).selector(PickingAgent(picks)).pick([Tickets().plain("ENG-1")], SLOTS, {}))

        assert "serve: acme: the selector picked 1 in acme/app, not a ticket of the shortlist with that repo: skipped" in caplog.text
        assert "serve: acme: the selector picked ENG-1 in evil/repo, not a ticket of the shortlist with that repo: skipped" in caplog.text

    def test_no_free_slot_or_no_candidate_calls_no_model(self, tmp_path: Path) -> None:
        agent = PickingAgent([])
        selector = Setup(tmp_path).selector(agent)

        assert (asyncio.run(selector.pick([Tickets().plain("ENG-1")], 0, {})), asyncio.run(selector.pick([], SLOTS, {})), agent.requests) == ([], [], [])

    def test_more_picks_than_free_slots_keeps_the_slots(self, tmp_path: Path) -> None:
        tickets = Tickets()
        keys = [f"ENG-{index}" for index in range(1, MANY + 1)]
        agent = PickingAgent([{"key": key, "repo": "acme/app", "reason": "r"} for key in keys])

        picked = asyncio.run(Setup(tmp_path).selector(agent).pick([tickets.plain(key) for key in keys], SLOTS, {}))

        assert [item.key for item in picked] == keys[:SLOTS]


class NoneFree:
    """An open MR rule that finds an open MR for every ticket."""

    def free(self, items: Sequence[WorkItem]) -> List[WorkItem]:
        return []


class DeadChat:
    """A chat whose session the workspace refused (a real Slack `invalid_auth`)."""

    def asks(self) -> Any:
        raise LoginRejected("slack auth.test: invalid_auth")

    def talk(self, items: Any, asks: Any) -> Any:
        raise LoginRejected("slack auth.test: invalid_auth")


class TestChoice:
    def test_a_refused_chat_login_skips_the_chat_and_the_choice_goes_on(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Decision B3 of the autonomous serve: Slack is optional; a dead session drops the
        chat asks for that poll instead of failing every poll."""
        setup = Setup(tmp_path)
        agent = PickingAgent([{"key": "ENG-1", "repo": "acme/app", "reason": "the only ticket"}])
        choice = Choice(setup.selector(agent), setup.company(), lambda: TODAY, DeadChat())  # type: ignore[arg-type]  # a ChatReader stand-in

        picked = asyncio.run(choice.choose([Tickets().plain("ENG-1")], SLOTS))

        assert [item.key for item in picked] == ["ENG-1"]
        assert "serve: acme: the chat was skipped on this poll: slack auth.test: invalid_auth" in caplog.text

    def test_tickets_with_an_open_mr_never_reach_the_model(self, tmp_path: Path) -> None:
        """N6 (M4): the rule runs before the score; nothing left means no model call."""
        setup, agent = Setup(tmp_path), PickingAgent([])
        choice = Choice(setup.selector(agent), setup.company(), lambda: TODAY, open_work=NoneFree())

        assert (asyncio.run(choice.choose([Tickets().plain("ENG-1")], SLOTS)), agent.requests) == ([], [])

    def test_a_poll_with_the_same_shortlist_and_slots_asks_the_model_once(self, tmp_path: Path) -> None:
        """A ticket the model skipped must not cost a model call on every poll."""
        setup, tickets = Setup(tmp_path), Tickets()
        agent = PickingAgent([{"key": "ENG-1", "repo": "none", "reason": "no repo fits"}])
        choice = Choice(setup.selector(agent), setup.company(), lambda: TODAY)
        found = [tickets.plain("ENG-1"), tickets.item("ENG-2", {"status": "Blocked"})]

        async def polls() -> List[List[WorkItem]]:
            return [await choice.choose(found, SLOTS), await choice.choose(found, SLOTS), await choice.choose(found, 1)]

        assert asyncio.run(polls()) == [[], [], []]
        assert [request.prompt.count("\n- ENG-") for request in agent.requests] == [1, 1]

    def test_a_chat_ask_moves_a_ticket_into_the_shortlist_and_its_thread_reaches_the_model(self, tmp_path: Path) -> None:
        """T014, in a fake: ENG-7 is the newest of 7 plain tickets, so the score alone leaves it out of the 5."""
        setup, tickets = Setup(tmp_path), Tickets()
        chat = FakeChat([ChatMessage("U2", "C1", "1.0", "can someone take ENG-7? the release waits on it", "ana")])
        agent = PickingAgent([{"key": "ENG-7", "repo": "acme/app", "reason": "asked in chat"}])
        choice = Choice(setup.selector(agent), setup.company(), lambda: TODAY, ChatReader(chat, setup.settings(), lambda: TODAY))
        found = [tickets.item(f"ENG-{index}", {"created_at": f"2026-01-0{index}"}) for index in range(1, MANY + 1)]

        picked = asyncio.run(choice.choose(found, SLOTS))

        prompt = agent.requests[0].prompt
        assert [item.key for item in picked] == ["ENG-7"] and prompt.index("\n- ENG-7") < prompt.index("\n- ENG-1")
        assert f"{Shortlist.CHAT}\n\n### ENG-7\nana in C1: can someone take ENG-7? the release waits on it" in prompt

    def test_a_new_chat_ask_asks_the_model_again(self, tmp_path: Path) -> None:
        setup, tickets = Setup(tmp_path), Tickets()
        chat = FakeChat([])
        agent = PickingAgent([])
        choice = Choice(setup.selector(agent), setup.company(), lambda: TODAY, ChatReader(chat, setup.settings(), lambda: TODAY))
        found = [tickets.plain("ENG-1")]

        async def polls() -> None:
            await choice.choose(found, SLOTS)
            await choice.choose(found, SLOTS)
            chat.messages.append(ChatMessage("U2", "C1", "2.0", "ENG-1 is blocking us", "ana"))
            await choice.choose(found, SLOTS)

        asyncio.run(polls())
        assert len(agent.requests) == ASKED_TWICE
