"""The state of a PR of briar through pauses, money stops and reopens (hardening PR, audit findings 1 to 4, 12 and
the mutants m05 to m09, m13, m18, m19): what a stop leaves in the worktree, which drafts briar may finish, and the CI
that a round owes. Each test names the audit finding or the mutant it kills."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from briar.extract._provider import CiState, PullState
from briar.serve.git import Shell
from briar.serve.state import MoneyStop, WorkEntry, WorkSource, WorkStatus
from briar.serve.work import BudgetReopen, WorkDay, WorkFinder, WorkKind
from tests.unit.serve.fakes import Companies, FakeGithub, FakeJira, FakeSources
from tests.unit.serve.test_review import CAP, LITTLE, MAX_USD, RAISED_USD, STOP, Opened, Reviews
from tests.unit.serve.ticket_fakes import Entries, FakeGithubSide, Files, Items, Plans, ScriptedAgent, Settings, Setup, Setups, Step

PR = FakeGithubSide.NUMBER
# The requests of a round that answers and then waits for the CI to be fixed.
TWO_REQUESTS = 2
# What an earlier run of the ticket already spent.
SPENT_BEFORE = 2.0
PR_NUMBER = 12
# A cap that leaves 0.05 after a ticket (0.3), a first CI fix (0.1), which fails the verify commands.
CAP_FOR_TWO_FIXES = 0.45
# A cap that leaves 0.05 after the answer and the self review of a round whose ticket booked 0.3 and 0.2.
CAP_FOR_THE_FIX = 0.55
# The cap of a day that already booked a money stop of 5.0 plus the ticket, leaving 0.05 after an answer and a review.
CAP_AFTER_A_MONEY_STOP = 5.55
ENOUGH = 100.0
STOPPED_AT = 5.0
RAISED_CAP = 8.0
READY_AT = "2026-11-18T10:00:00+00:00"
NOTHING_LEFT = "0"


@pytest.fixture
def opened(setups: Setups) -> Opened:
    return Opened(setups)


@dataclass(frozen=True)
class Worktree:
    """The local worktree of `briar/ENG-1` of a setup."""

    path: Path

    @classmethod
    def of(cls, setup: Setup) -> "Worktree":
        return cls(setup.cache / "worktrees" / "acme" / "briar__ENG-1")

    def head(self) -> str:
        return Shell().line("git rev-parse HEAD", self.path).output.strip()

    def status(self) -> str:
        return Shell().line("git status --porcelain", self.path).output

    def ahead_of_origin(self) -> str:
        return Shell().line("git rev-list --count origin/briar/ENG-1..HEAD", self.path).output.strip()


class TestAPauseDropsWhatWasNotPushed:
    """Audit 1: `TaskFlow.verified` commits BEFORE it verifies, so `HEAD` can hold a commit that failed verify. A pause must go
    back to the pushed head: the next `sync` is a fast forward only, and fails for ever when a reviewer pushed on the branch."""

    def paused(self, setups: Setups) -> Setup:
        setup = setups.make({"verify": ["test ! -f bad.txt"]}).day_capped(CAP_FOR_TWO_FIXES)
        setup.sources.github_side.ci[:] = [CiState.FAILED]
        broken = Step(writes={"bad.txt": "breaks verify\n"})
        stopped = Step(cost=LITTLE, failed=True, reason=STOP)
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), broken, stopped]), Items().jira())
        return setup

    def test_the_local_branch_has_nothing_the_origin_does_not(self, setups: Setups) -> None:
        setup = self.paused(setups)

        assert Worktree.of(setup).ahead_of_origin() == NOTHING_LEFT

    def test_the_next_day_goes_on_after_a_reviewer_pushed_on_the_branch(self, setups: Setups) -> None:
        setup = self.paused(setups)
        setup.origin.push_commit("briar/ENG-1", {"suggested.txt": "from the reviewer\n"})
        raised = setup.day_capped(CAP)
        raised.sources.github_side.ci[:] = [CiState.PASSED]

        raised.work_on(ScriptedAgent([]), Items().ready(PR))

        assert raised.entry("ENG-1").status is WorkStatus.DONE


class TestReadyIsForTheDraftsOfBriar:
    """Audit 2, through the finder (the old test called `work_on` and skipped it): a draft that a person made is not finished by briar."""

    def found(self, tmp_path: Path, entry: Dict[str, Any]) -> List[Tuple[WorkKind, int]]:
        company = Companies(tmp_path / "knowledge").company(Settings().block([]))
        fields: Dict[str, Any] = {"key": "ENG-9", "source": WorkSource.JIRA, "repo": "acme/app", "title": "t", "status": WorkStatus.PR_OPEN, "pr": PR_NUMBER}
        asyncio.run(company.state.record(WorkEntry(**{**fields, **entry})))
        github = FakeGithub(pulls={("acme/app", PR_NUMBER): PullState(True, False, False, "main", draft=True)})
        items = WorkFinder(company, FakeSources(github, jira_adapter=FakeJira({"ENG": []}))).find()
        return [(item.kind, item.pr) for item in items]

    def test_a_person_draft_after_a_money_stop_is_left_alone(self, tmp_path: Path) -> None:
        stop = MoneyStop(cap=STOPPED_AT, resume_to="pr-open", drafted=False)

        assert self.found(tmp_path, {"ready_at": READY_AT, "money_stop": stop}) == []

    def test_a_person_draft_of_a_pr_that_was_ready_is_left_alone(self, tmp_path: Path) -> None:
        assert self.found(tmp_path, {"ready_at": READY_AT}) == []

    def test_a_draft_that_was_never_ready_is_finished(self, tmp_path: Path) -> None:
        """`serve` stopped during the CI wait of the ticket run."""
        assert self.found(tmp_path, {}) == [(WorkKind.READY, PR_NUMBER)]

    def test_a_draft_that_briar_made_at_a_money_stop_is_finished(self, tmp_path: Path) -> None:
        stop = MoneyStop(cap=STOPPED_AT, resume_to="pr-open", drafted=True)

        assert self.found(tmp_path, {"ready_at": READY_AT, "money_stop": stop}) == [(WorkKind.READY, PR_NUMBER)]


class Rounds:
    """Review rounds on the PR of `Opened`, that stop for money or for the day."""

    def after_the_replies_the_day_stops_the_ci_fix(self, opened: Opened) -> Setup:
        setup, reviews = opened.make({}), Reviews()
        setup.sources.github_side.ci[:] = [CiState.FAILED]
        capped = setup.day_capped(CAP_FOR_THE_FIX)
        rows = [{"id": "inline:1", "changed": True, "reply": "Added."}]
        answer = Step(writes={"feature.txt": "done, see ENG-1\n"}, structured=reviews.answers(rows))
        fix = Step(writes={"other.txt": "x\n"}, cost=LITTLE, failed=True, reason=STOP)
        capped.work_on(ScriptedAgent([answer, Plans().approved(), fix]), reviews.item([reviews.inline()]))
        return capped

    def money_stop_in_the_ci_fix(self, opened: Opened) -> Setup:
        """A round whose CI fix spends the whole `max_usd`: the PR is a draft that briar made, the CI is red."""
        setup, reviews = opened.make({}), Reviews()
        setup.sources.github_side.ci[:] = [CiState.FAILED]
        rows = [{"id": "inline:1", "changed": True, "reply": "Added."}]
        answer = Step(writes={"feature.txt": "done, see ENG-1\n"}, structured=reviews.answers(rows))
        setup.work_on(ScriptedAgent([answer, Plans().approved(), Step(cost=MAX_USD)]), reviews.item([reviews.inline()]))
        return setup

    def reply_only(self, text: str) -> Step:
        return Step(structured=Reviews().answers([{"id": "conversation:2", "changed": False, "reply": text}]))


class TestTheCiThatARoundOwes:
    """Audit findings 3 and 4: `ci_paused` and a draft that briar made both mean that the CI has not passed yet, and a round that
    changes no code used to skip the CI wait, clear the marker and mark the PR ready with a red CI."""

    def test_a_reply_only_round_runs_the_ci_fix_that_the_day_pause_owes(self, opened: Opened) -> None:
        paused = Rounds().after_the_replies_the_day_stops_the_ci_fix(opened)
        raised, github, reviews = paused.day_capped(CAP), paused.sources.github_side, Reviews()
        github.ci[:] = [CiState.FAILED, CiState.PASSED]
        agent = ScriptedAgent([Rounds().reply_only("It is fine."), Step(writes={"ci-fix.txt": "x\n"})])

        raised.work_on(agent, reviews.item([reviews.talk()]))

        entry = raised.entry("ENG-1")
        assert (len(agent.requests), entry.status, entry.ci_paused) == (TWO_REQUESTS, WorkStatus.DONE, False)

    def test_a_reply_only_round_does_not_wait_for_a_ci_it_does_not_owe(self, opened: Opened) -> None:
        """The shortcut stays for the common case: no marker, no draft of briar."""
        setup, reviews = opened.make({}), Reviews()
        setup.sources.github_side.ci[:] = [CiState.FAILED]
        agent = ScriptedAgent([Rounds().reply_only("It is fine.")])

        setup.work_on(agent, reviews.item([reviews.talk()]))

        assert (len(agent.requests), setup.entry("ENG-1").status) == (1, WorkStatus.DONE)

    def test_a_draft_of_briar_is_not_marked_ready_by_a_reply_only_round_while_the_ci_is_red(self, opened: Opened) -> None:
        stopped = Rounds().money_stop_in_the_ci_fix(opened)
        raised, github, reviews = stopped.raised(RAISED_USD), stopped.sources.github_side, Reviews()
        asyncio.run(BudgetReopen(raised.company).apply())
        github.ci[:] = [CiState.FAILED, CiState.PASSED]
        ready_before = list(github.readies)
        agent = ScriptedAgent([Rounds().reply_only("Fine as it is."), Step(writes={"ci-fix.txt": "x\n"})])

        raised.work_on(agent, reviews.item([reviews.talk()]))

        assert (len(agent.requests), github.readies) == (TWO_REQUESTS, [*ready_before, PR])


class TestDraftMemoryAcrossAPause:
    """Mutants m18 and m19: the memory that briar made the draft survives a day pause, or the PR stays a draft for good."""

    def stopped_at_the_answer(self, opened: Opened) -> Setup:
        """The first call of the round spends the whole `max_usd`: the PR is a draft that briar made, and the cap is raised."""
        setup, reviews = opened.make({}), Reviews()
        setup.work_on(ScriptedAgent([Step(structured=reviews.answers([]), cost=MAX_USD)]), reviews.item([reviews.inline()]))
        raised = setup.raised(RAISED_USD)
        assert asyncio.run(BudgetReopen(raised.company).apply()) == ["ENG-1"]
        return raised

    def test_a_briar_draft_is_marked_ready_after_a_day_pause_that_followed_a_money_stop(self, opened: Opened) -> None:
        raised, reviews = self.stopped_at_the_answer(opened), Reviews()
        github = raised.sources.github_side
        github.ci[:] = [CiState.FAILED]
        capped = raised.day_capped(CAP_AFTER_A_MONEY_STOP)
        rows = [{"id": "inline:1", "changed": True, "reply": "Added."}]
        answer = Step(writes={"feature.txt": "done, see ENG-1\n"}, structured=reviews.answers(rows))
        fix = Step(writes={"other.txt": "x\n"}, cost=LITTLE, failed=True, reason=STOP)
        capped.work_on(ScriptedAgent([answer, Plans().approved(), fix]), reviews.item([reviews.inline()]))
        assert capped.entry("ENG-1").ci_paused is True
        github.ci[:] = [CiState.PASSED]
        ready_before = list(github.readies)

        capped.day_capped(ENOUGH).work_on(ScriptedAgent([]), reviews.resumed())

        assert github.readies == [*ready_before, PR]

    def test_a_pause_before_the_replies_keeps_the_money_stop_of_the_entry(self, opened: Opened) -> None:
        raised, reviews = self.stopped_at_the_answer(opened), Reviews()
        capped = raised.day_capped(CAP_AFTER_A_MONEY_STOP)
        answer = Step(structured=reviews.answers([]), cost=LITTLE, failed=True, reason=STOP)

        capped.work_on(ScriptedAgent([answer]), reviews.item([reviews.inline()]))

        stop = capped.entry("ENG-1").money_stop
        assert ("ENG-1 paused" in capped.company.notes[WorkDay().today()][-1], stop is not None and stop.drafted) == (True, True)


class TestAStopDropsTheLocalCommits:
    """Mutants m05 to m07: a stop in the self review fix of a round goes back to the pushed head. The answer commit of the round is
    local, so a pause that only drops uncommitted edits would keep it and push it unreviewed with the next round."""

    def rows(self) -> List[Dict[str, Any]]:
        return [{"id": "inline:1", "changed": True, "reply": "Added."}]

    def round_with(self, setup: Setup, fix: Step) -> None:
        reviews = Reviews()
        answer = Step(writes={"feature.txt": "unverified\n"}, structured=reviews.answers(self.rows()))
        setup.work_on(ScriptedAgent([answer, Plans().rejected("add a test"), fix]), reviews.item([reviews.inline()]))

    def test_a_day_stop_in_the_self_review_fix_leaves_the_pushed_head_and_a_clean_tree(self, opened: Opened) -> None:
        setup = opened.make({})
        head = opened.head(setup)
        capped = setup.day_capped(CAP_FOR_THE_FIX)

        self.round_with(capped, Step(writes={"x.txt": "x\n"}, cost=LITTLE, failed=True, reason=STOP))

        worktree = Worktree.of(capped)
        assert (opened.head(capped), worktree.head(), worktree.status(), capped.entry("ENG-1").rounds) == (head, head, "", 0)

    def test_a_ticket_cap_in_the_self_review_fix_leaves_the_pushed_head_and_nothing_pushed(self, opened: Opened) -> None:
        setup = opened.make({})
        head = opened.head(setup)

        self.round_with(setup, Step(writes={"x.txt": "x\n"}, cost=MAX_USD))

        worktree = Worktree.of(setup)
        assert (opened.head(setup), worktree.head(), worktree.status(), setup.entry("ENG-1").status) == (head, head, "", WorkStatus.BLOCKED)

    def test_a_day_stop_in_the_conflict_fix_leaves_no_merge_in_progress(self, opened: Opened) -> None:
        setup = opened.make({})
        head = opened.head(setup)
        setup.origin.push_commit("main", {"feature.txt": "other\n"})
        capped = setup.day_capped(CAP_FOR_THE_FIX - LITTLE - LITTLE)

        capped.work_on(ScriptedAgent([Step(cost=LITTLE, failed=True, reason=STOP)]), Reviews().conflicted())

        worktree = Worktree.of(capped)
        in_progress = Shell().line("git rev-parse -q --verify MERGE_HEAD", worktree.path).ok()
        assert (worktree.head(), worktree.status(), in_progress) == (head, "", False)


class TestWhereAStopResumesTo:
    """Mutant m13: `resume_to` is the status the ticket was in, not always `planned`."""

    def stopped(self, setups: Setups, steps: List[Step], ci: List[CiState]) -> WorkEntry:
        setup = setups.make({})
        setup.sources.github_side.ci[:] = ci
        setup.work_on(ScriptedAgent(steps), Items().jira())
        entry = setup.entry("ENG-1")
        assert entry.money_stop is not None
        return entry

    def test_a_stop_in_the_first_call_resumes_to_found(self, setups: Setups) -> None:
        entry = self.stopped(setups, [Step(cost=MAX_USD)], [CiState.PASSED])

        assert entry.money_stop is not None and entry.money_stop.resume_to == "found"

    def test_a_stop_in_the_build_resumes_to_planned(self, setups: Setups) -> None:
        entry = self.stopped(setups, [Plans().plan(), Step(cost=MAX_USD)], [CiState.PASSED])

        assert entry.money_stop is not None and entry.money_stop.resume_to == "planned"

    def test_a_stop_in_the_ci_fix_resumes_to_pr_open(self, setups: Setups) -> None:
        steps = [Plans().plan(), Step(writes=Files().feature()), Plans().approved(), Step(cost=MAX_USD)]

        entry = self.stopped(setups, steps, [CiState.FAILED])

        assert entry.money_stop is not None and entry.money_stop.resume_to == "pr-open"


class TestAReopenAndALaterStop:
    """Mutants m08 and m09."""

    def test_a_ticket_that_runs_again_is_not_rewound_by_the_next_poll(self, tmp_path: Path) -> None:
        company = Companies(tmp_path / "knowledge").company({**Settings().block([]), "max_usd": RAISED_CAP})
        running = (
            Entries()
            .planned(spent=1.0)
            .model_copy(update={"status": WorkStatus.PR_OPEN, "pr": PR, "money_stop": MoneyStop(cap=STOPPED_AT, resume_to="planned")})
        )
        asyncio.run(company.state.record(running))

        assert (asyncio.run(BudgetReopen(company).apply()), company.state.book().entry("ENG-1").status) == ([], WorkStatus.PR_OPEN)

    def test_another_stop_clears_the_money_stop_so_a_later_raise_does_not_reopen_it(self, setups: Setups) -> None:
        setup = setups.make({"max_usd": RAISED_CAP})
        setup.save(Entries().planned(spent=SPENT_BEFORE).model_copy(update={"money_stop": MoneyStop(cap=STOPPED_AT, resume_to="planned")}))

        setup.work_on(ScriptedAgent([Step()]), Items().jira())

        entry = setup.entry("ENG-1")
        assert (entry.status, entry.money_stop) == (WorkStatus.BLOCKED, None)
        assert asyncio.run(BudgetReopen(setup.raised(CAP).company).apply()) == []
