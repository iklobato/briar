"""How `briar serve` chooses its next tickets (spec 008 PR 1: N2 to N7, N9 to N11, Q1 to Q3):
the tickets nobody can work now are dropped, the rest are scored, the model picks among the
5 best. No network: the agent is scripted."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from briar.extract._tracker import Ticket
from briar.iac.runbook.models import ServeSettings
from briar.serve.agent import AgentAnswer, AgentRequest
from briar.serve.select import Choice, Score, Selector, Workable
from briar.serve.state import WorkSource
from briar.serve.work import ServeCompany, WorkItem, WorkKind
from tests.unit.serve.fakes import Companies

TODAY = date(2026, 11, 12)
SELECTION_COST = 0.04
TOP = 5
SLOTS = 2
MANY = 7
UNBLOCKS_THREE = 3
UNBLOCKS_FIVE = 5
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

        picked = asyncio.run(Setup(tmp_path).selector(agent).pick([tickets.plain(f"ENG-{index}") for index in range(1, MANY + 1)], SLOTS))

        assert len(agent.requests) == 1 and agent.requests[0].prompt.count("\n- ENG-") == TOP
        assert [(item.key, item.repo, item.reason, item.selection_cost) for item in picked] == [("ENG-3", "acme/api", "unblocks the release", SELECTION_COST)]

    def test_a_pick_outside_the_allowed_repos_or_the_shortlist_is_skipped(self, tmp_path: Path) -> None:
        tickets = Tickets()
        picks = [{"key": "ENG-1", "repo": "evil/repo", "reason": "x"}, {"key": "ENG-9", "repo": "acme/app", "reason": "y"}, {"key": "ENG-2", "repo": "none", "reason": "no repo fits"}]

        picked = asyncio.run(Setup(tmp_path).selector(PickingAgent(picks)).pick([tickets.plain("ENG-1"), tickets.plain("ENG-2")], SLOTS))

        assert picked == []

    def test_no_free_slot_or_no_candidate_calls_no_model(self, tmp_path: Path) -> None:
        agent = PickingAgent([])
        selector = Setup(tmp_path).selector(agent)

        assert (asyncio.run(selector.pick([Tickets().plain("ENG-1")], 0)), asyncio.run(selector.pick([], SLOTS)), agent.requests) == ([], [], [])

    def test_more_picks_than_free_slots_keeps_the_slots(self, tmp_path: Path) -> None:
        tickets = Tickets()
        keys = [f"ENG-{index}" for index in range(1, MANY + 1)]
        agent = PickingAgent([{"key": key, "repo": "acme/app", "reason": "r"} for key in keys])

        picked = asyncio.run(Setup(tmp_path).selector(agent).pick([tickets.plain(key) for key in keys], SLOTS))

        assert [item.key for item in picked] == keys[:SLOTS]


class TestChoice:
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
