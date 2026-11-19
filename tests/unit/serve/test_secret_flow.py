"""WP2 of the production plan (P33): no push adds a secret. A hit before a push gives the agent ONE fix loop (it is told the file and the line, never the
value); a clean fix is pushed as one commit, so the commit that held the value is not in the history of the origin either; a second hit blocks the
task with no push. The ticket publish, the CI fix, a review round and the draft a stop makes are all covered."""

from __future__ import annotations

from typing import Any, List

import pytest

from briar.extract._provider import CiState
from briar.serve.git import Shell
from briar.serve.state import WorkStatus
from tests.unit.serve.test_review import Opened, Reviews
from tests.unit.serve.test_secret_scan import Keys
from tests.unit.serve.ticket_fakes import Files, Items, Plans, ScriptedAgent, Setup, Setups, Step

BRANCH = "briar/ENG-1"
NO_OTHER = "test ! -e other.txt"
CLEAN = "key = <redacted>\n"
FIX_CALL = 3
PR = 41


class Origin:
    """What the origin of the test holds."""

    def history(self, setup: Setup) -> str:
        bare = setup.origin.root / "remotes" / "acme" / "app.git"
        return Shell().line("git log -p --all", bare).output

    def branch_exists(self, setup: Setup) -> bool:
        return BRANCH in setup.origin.branches()


class Writes:
    """The files a scripted agent writes."""

    def secret(self) -> Step:
        return Step(writes={**Files().feature(), "conf.txt": f"key = {Keys().aws()}\n"})

    def removed(self) -> Step:
        return Step(writes={"conf.txt": CLEAN})

    def again(self) -> Step:
        return Step(writes={"conf.txt": f"key = {Keys().aws()}\n"})


@pytest.fixture
def opened(setups: Setups) -> Opened:
    return Opened(setups)


class TestTicketPublish:
    def test_one_fix_loop_removes_the_secret_and_the_history_of_the_origin_stays_clean(self, setups: Setups) -> None:
        setup = setups.make({})
        agent = ScriptedAgent([Plans().plan(), Writes().secret(), Plans().approved(), Writes().removed()])

        setup.work_on(agent, Items().jira())

        history = Origin().history(setup)
        assert (setup.entry("ENG-1").status, len(setup.sources.github_side.pulls)) == (WorkStatus.DONE, 1)
        assert Keys().aws() not in history
        assert "<redacted>" in history

    def test_the_agent_is_told_the_file_and_the_line_and_never_the_value(self, setups: Setups) -> None:
        setup = setups.make({})
        agent = ScriptedAgent([Plans().plan(), Writes().secret(), Plans().approved(), Writes().removed()])

        setup.work_on(agent, Items().jira())

        prompt = agent.requests[FIX_CALL].prompt
        assert "conf.txt:1" in prompt
        assert Keys().aws() not in prompt

    def test_a_second_hit_blocks_the_task_with_nothing_pushed_and_the_value_nowhere(self, setups: Setups) -> None:
        setup = setups.make({})
        agent = ScriptedAgent([Plans().plan(), Writes().secret(), Plans().approved(), Writes().again()])

        setup.work_on(agent, Items().jira())

        entry, github, tracker = setup.entry("ENG-1"), setup.sources.github_side, setup.sources.tracker_side
        written = " ".join([entry.report, *[text for _, _, text in github.comments], *[text for _, text in tracker.comments]])
        assert (entry.status, github.pulls, Origin().branch_exists(setup)) == (WorkStatus.BLOCKED, [], False)
        assert "conf.txt:1" in written and "1 of 3 fix loops" in written
        assert Keys().aws() not in written

    def test_a_clean_change_makes_no_fix_call(self, setups: Setups) -> None:
        setup = setups.make({})
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()])

        setup.work_on(agent, Items().jira())

        assert (setup.entry("ENG-1").status, len(agent.requests)) == (WorkStatus.DONE, FIX_CALL)


class TestTheOtherPushes:
    def test_a_ci_fix_that_adds_a_secret_is_cleaned_before_it_is_pushed(self, setups: Setups) -> None:
        setup = setups.make({})
        setup.sources.github_side.ci[:] = [CiState.FAILED, CiState.PASSED]
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), Writes().secret(), Writes().removed()])

        setup.work_on(agent, Items().jira())

        assert (setup.entry("ENG-1").status, Keys().aws() in Origin().history(setup)) == (WorkStatus.DONE, False)

    def test_a_review_round_that_adds_a_secret_is_cleaned_before_it_is_pushed(self, opened: Opened) -> None:
        setup, reviews = opened.make({}), Reviews()
        rows: List[Any] = [{"id": "inline:1", "changed": True, "reply": "Added."}]
        answer = Step(writes={"conf.txt": f"key = {Keys().aws()}\n"}, structured=reviews.answers(rows))

        setup.work_on(ScriptedAgent([answer, Plans().approved(), Writes().removed()]), reviews.item([reviews.inline()]))

        assert (setup.entry("ENG-1").status, Keys().aws() in Origin().history(setup)) == (WorkStatus.DONE, False)

    def test_the_draft_a_stop_makes_is_not_pushed_when_it_holds_a_secret(self, setups: Setups) -> None:
        """The verify never passes, so the task stops at `max_loops` and briar would push the work so far as a draft: not with a secret in it."""
        setup = setups.make({"max_loops": 1, "verify": [NO_OTHER]})
        leak = Step(writes={"other.txt": f"key = {Keys().aws()}\n"})
        agent = ScriptedAgent([Plans().plan(), leak, leak])

        setup.work_on(agent, Items().jira())

        entry, github = setup.entry("ENG-1"), setup.sources.github_side
        assert (entry.status, github.pulls, Origin().branch_exists(setup)) == (WorkStatus.BLOCKED, [], False)
        assert "nothing was pushed" in entry.report
        assert Keys().aws() not in entry.report
