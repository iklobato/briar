"""WP3 of the production plan (P6; audit item 10): a pause in the verify or the self review of a ticket with no PR used to repeat the whole build call
when the ticket came back, because the status was still `planned`. The build commits when it finishes, so a branch with work of its own is built
already. A pause inside the build leaves uncommitted edits and no commit, so that build still runs, on the kept edits."""

from __future__ import annotations

import asyncio

from briar.extract._provider import BranchPull
from briar.serve.agent import Access
from briar.serve.state import WorkStatus
from briar.serve.work import BudgetReopen
from tests.unit.serve.ticket_fakes import Entries, FakeGithubSide, Files, Items, Plans, ScriptedAgent, Setup, Setups, Step

STOP = "error_max_budget_usd"
# A day cap that fits the plan and the build (0.1 each) and leaves 0.05 for the next call, one that fits only the plan, and a cap with room.
AFTER_THE_BUILD = 0.25
LITTLE = 0.05
AFTER_THE_PLAN = 0.15
CAP = 10.0
PR = FakeGithubSide.NUMBER
# ENG-1 had spent 4.8 of 5.0, the build costs 0.3 (over the cap), and the cap after the raise.
SPENT_BEFORE = 4.8
BUILD_COST = 0.3
RAISED_USD = 8.0


class Paused:
    """A ticket that the day cap paused, and then the same ticket with room in the day."""

    def in_the_review(self, setups: Setups) -> Setup:
        capped = setups.make({}).day_capped(AFTER_THE_BUILD)
        stopped = Step(cost=LITTLE, failed=True, reason=STOP)
        capped.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), stopped]), Items().jira())
        return capped.day_capped(CAP)

    def in_the_build(self, setups: Setups) -> Setup:
        capped = setups.make({}).day_capped(AFTER_THE_PLAN)
        stopped = Step(writes={"wip.txt": "half\n"}, cost=LITTLE, failed=True, reason=STOP)
        capped.work_on(ScriptedAgent([Plans().plan(), stopped]), Items().jira())
        return capped.day_capped(CAP)


class TestAStopInsideTheBuild:
    def test_a_ticket_that_ran_out_of_money_inside_the_build_builds_again_on_the_kept_work(self, setups: Setups) -> None:
        """The draft that a stop makes commits `work so far`, so a commit does not say the build ended: only the entry does."""
        setup = setups.make({})
        setup.save(Entries().planned(spent=SPENT_BEFORE))
        setup.work_on(ScriptedAgent([Step(writes=Files().feature(), cost=BUILD_COST)]), Items().jira())
        raised = setup.raised(RAISED_USD)
        raised.sources.github_side.branch_pulls["briar/ENG-1"] = BranchPull(PR, open=True, merged=False, draft=True)
        asyncio.run(BudgetReopen(raised.company).apply())
        agent = ScriptedAgent([Step(writes={"more.txt": "more\n"}), Plans().approved()])

        raised.work_on(agent, Items().jira())

        assert (raised.entry("ENG-1").status, [request.access for request in agent.requests]) == (WorkStatus.DONE, [Access.EDIT, Access.READ])


class TestAPauseRepeatsNoWork:
    def test_a_pause_in_the_self_review_does_not_build_again(self, setups: Setups) -> None:
        resumed = Paused().in_the_review(setups)
        agent = ScriptedAgent([Plans().approved()])

        resumed.work_on(agent, Items().jira())

        assert (resumed.entry("ENG-1").status, [request.access for request in agent.requests]) == (WorkStatus.DONE, [Access.READ])

    def test_a_pause_inside_the_build_still_builds_on_the_kept_edits(self, setups: Setups) -> None:
        resumed = Paused().in_the_build(setups)
        agent = ScriptedAgent([Step(writes=Files().feature()), Plans().approved()])

        resumed.work_on(agent, Items().jira())

        assert (resumed.entry("ENG-1").status, [request.access for request in agent.requests]) == (WorkStatus.DONE, [Access.EDIT, Access.READ])
