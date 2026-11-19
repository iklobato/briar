"""WP1 of the production plan (P13, P21, P29 to P32): a call the model refused, because its login was refused or the plan limit
is hit, pauses the task like the day cap does. Nothing is written on the ticket or the PR, the status stays, and what was never
verified goes. A refused login also asks `serve` to stop; a limit closes the gate of the plan."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

import pytest

from briar.extract._provider import CiState
from briar.serve.agent import AgentAnswer, AgentRequest, Refusal
from briar.serve.git import Shell
from briar.serve.select import Choice, Selector
from briar.serve.state import WorkStatus
from briar.serve.work import WorkDay, WorkItem
from tests.unit.serve.fakes import Companies
from tests.unit.serve.test_review import Opened, Reviews
from tests.unit.serve.test_select import Tickets
from tests.unit.serve.ticket_fakes import FakeGithubSide, Files, Items, Plans, ScriptedAgent, Setup, Setups, Step

LOGIN_REASON = "Claude Code returned an error result: Invalid API key (exit code: 1)"
LIMIT_REASON = "Claude Code returned an error result: You have hit your limit (exit code: 1)"
PR = FakeGithubSide.NUMBER
# What the refused call cost (a refused login costs nothing, a limit call may have cost a little), and one scripted step.
SMALL = 0.02
STEP = 0.1
# A day cap of 0.35 leaves 0.05 after the three steps of the ticket phase, for the CI fix that gets refused.
CAP_AFTER_THE_TICKET = 0.35
ONCE = 1
ASKED_TWICE = 2


@dataclass(frozen=True)
class Kind:
    """One refusal, the reason the CLI gave, the note it leaves in the daily report, and whether it stops `serve`."""

    refusal: Refusal
    reason: str
    note: str
    stops: bool

    def step(self, cost: float = 0.0) -> Step:
        return Step(cost=cost, failed=True, reason=self.reason, refusal=self.refusal)


class Kinds:
    """The two refusals of the model, as the tests run them."""

    def login(self) -> Kind:
        note = f"ENG-1 paused: the login of the model was refused: {LOGIN_REASON}; serve stops now: renew the login and start it again"
        return Kind(Refusal.LOGIN, LOGIN_REASON, note, True)

    def limit(self) -> Kind:
        note = f"ENG-1 paused: the plan limit is hit: {LIMIT_REASON}; it goes on when the limit is lifted"
        return Kind(Refusal.LIMIT, LIMIT_REASON, note, False)

    def both(self) -> List[Kind]:
        return [self.login(), self.limit()]

    def ids(self) -> List[str]:
        return ["login", "limit"]


@pytest.fixture
def opened(setups: Setups) -> Opened:
    return Opened(setups)


class Silence:
    """What briar wrote on the ticket and the PR: nothing, when a task is paused."""

    def of(self, setup: Setup) -> Tuple[object, ...]:
        github, tracker = setup.sources.github_side, setup.sources.tracker_side
        return github.pulls, github.comments, github.drafts, github.readies, tracker.comments, tracker.moves


class TestTicketRefused:
    @pytest.mark.parametrize("kind", Kinds().both(), ids=Kinds().ids())
    def test_nothing_is_written_and_the_ticket_is_not_blocked(self, kind: Kind, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(ScriptedAgent([kind.step()]), Items().jira())

        entry = setup.entry("ENG-1")
        assert (Silence().of(setup), entry.status, entry.report, entry.money_stop) == (([], [], [], [], [], []), WorkStatus.FOUND, "", None)

    @pytest.mark.parametrize("kind", Kinds().both(), ids=Kinds().ids())
    def test_a_refusal_after_the_plan_keeps_the_status_and_books_what_the_call_cost(self, kind: Kind, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Plans().plan(), kind.step(SMALL)]), Items().jira())

        entry = setup.entry("ENG-1")
        assert (entry.status, entry.report, entry.spent_total()) == (WorkStatus.PLANNED, "", pytest.approx(STEP + SMALL))

    @pytest.mark.parametrize("kind", Kinds().both(), ids=Kinds().ids())
    def test_the_daily_report_gets_one_note_however_many_times_it_happens(self, kind: Kind, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(ScriptedAgent([kind.step()]), Items().jira())
        setup.work_on(ScriptedAgent([kind.step()]), Items().jira())

        assert setup.company.notes[WorkDay().today()] == [kind.note]

    def test_a_refused_login_asks_serve_to_stop_with_the_fix(self, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Kinds().login().step()]), Items().jira())

        dead = setup.company.stop.reason()
        assert dead is not None
        assert (setup.company.stop.asked(), dead.source, dead.company, dead.detail) == (True, "model", "acme", LOGIN_REASON)
        assert "CLAUDE_CODE_OAUTH_TOKEN" in dead.fix
        assert setup.company.gate.closed() is False

    def test_a_plan_limit_closes_the_gate_and_does_not_stop_serve(self, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Kinds().limit().step()]), Items().jira())

        assert (setup.company.gate.closed(), setup.company.stop.asked()) == (True, False)

    def test_a_call_that_works_opens_the_gate_again(self, setups: Setups) -> None:
        setup = setups.make({})
        setup.company.gate.hit("the plan limit is hit")

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        assert setup.company.gate.closed() is False

    @pytest.mark.parametrize("kind", Kinds().both(), ids=Kinds().ids())
    def test_with_a_pr_what_was_never_verified_is_dropped_and_the_ticket_is_not_blocked(self, kind: Kind, setups: Setups) -> None:
        """The CI of the PR is red and the fix call is refused: the unverified fix goes (the next sync is a fast forward only)."""
        setup = setups.make({})
        setup.sources.github_side.ci[:] = [CiState.FAILED]
        fix = Step(writes={"other.txt": "x\n"}, failed=True, reason=kind.reason, refusal=kind.refusal)

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), fix]), Items().jira())

        worktree = setup.cache / "worktrees" / "acme" / "briar__ENG-1"
        entry = setup.entry("ENG-1")
        assert (entry.status, entry.pr, entry.report) == (WorkStatus.PR_OPEN, PR, "")
        assert (not (worktree / "other.txt").exists(), Shell().line("git status --porcelain", worktree).output) == (True, "")
        assert (setup.sources.github_side.readies, setup.sources.github_side.drafts) == ([], [])


class TestRoundRefused:
    """A refusal in a review round gives the round back and leaves the PR as it was."""

    @pytest.mark.parametrize("kind", Kinds().both(), ids=Kinds().ids())
    def test_the_round_is_given_back_and_nothing_is_written_on_the_pr(self, kind: Kind, opened: Opened) -> None:
        setup, reviews = opened.make({}), Reviews()
        before = list(setup.sources.github_side.comments)
        refused = Step(writes={"feature.txt": "unverified\n"}, structured=reviews.answers([]), failed=True, reason=kind.reason, refusal=kind.refusal)

        setup.work_on(ScriptedAgent([refused]), reviews.item([reviews.inline()]))

        entry, github = setup.entry("ENG-1"), setup.sources.github_side
        assert (entry.status, entry.rounds, entry.answered, entry.money_stop, entry.ci_paused) == (WorkStatus.DONE, 0, {}, None, False)
        assert (github.replies, github.drafts, github.comments) == ([], [], before)


@dataclass(frozen=True)
class RefusedThenPicks:
    """The selector model: refuses the first call, answers the second."""

    first: Kind
    requests: List[AgentRequest] = field(default_factory=list)

    async def ask(self, request: AgentRequest) -> AgentAnswer:
        self.requests.append(request)
        if len(self.requests) == ONCE:
            return AgentAnswer("", {}, 0.0, failed=True, reason=self.first.reason, refusal=self.first.refusal)
        return AgentAnswer("", {"picks": [{"key": "ENG-1", "repo": "acme/app", "reason": "the only ticket"}]}, SMALL)


class TestSelectorRefused:
    """The selector call is a model call too: a refusal is not a failed call, so it is not remembered and is asked again."""

    def choose(self, kind: Kind, tmp_path: Path) -> Tuple[Any, RefusedThenPicks, List[WorkItem], List[WorkItem]]:
        company = Companies(tmp_path).company({"jira": {"ENG": {"repos": ["acme/app", "acme/api"]}}})
        agent = RefusedThenPicks(kind)
        choice, found = Choice(Selector(agent, company), company), [Tickets().plain("ENG-1")]
        first = asyncio.run(choice.choose(found, 1))
        return company, agent, first, asyncio.run(choice.choose(found, 1))

    @pytest.mark.parametrize("kind", Kinds().both(), ids=Kinds().ids())
    def test_a_refused_selector_is_asked_again_on_the_next_poll(self, kind: Kind, tmp_path: Path) -> None:
        _, agent, first, again = self.choose(kind, tmp_path)

        assert (first, [item.key for item in again], len(agent.requests)) == ([], ["ENG-1"], ASKED_TWICE)

    def test_a_refused_login_of_the_selector_asks_serve_to_stop(self, tmp_path: Path) -> None:
        company, _, _, _ = self.choose(Kinds().login(), tmp_path)

        dead: Optional[Any] = company.stop.reason()
        assert dead is not None
        assert (company.stop.asked(), dead.source) == (True, "model")

    def test_a_refused_selector_that_reached_the_plan_limit_closes_the_gate_until_a_call_works(self, tmp_path: Path) -> None:
        company = Companies(tmp_path).company({"jira": {"ENG": {"repos": ["acme/app", "acme/api"]}}})
        choice = Choice(Selector(RefusedThenPicks(Kinds().limit()), company), company)
        found = [Tickets().plain("ENG-1")]

        asyncio.run(choice.choose(found, 1))
        closed = company.gate.closed()
        asyncio.run(choice.choose(found, 1))

        assert (closed, company.gate.closed()) == (True, False)
