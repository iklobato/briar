"""WP3 of the production plan (P6, P39; audit item 8): a draft that briar made when a ticket ran out of money is briar to finish. A draft that a
person marked ready is theirs: once `max_usd` is raised the ticket flow pushes nothing more into it, the entry is DONE, and review comments are
still answered as on any PR of briar."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from briar.extract._provider import BranchPull
from briar.serve.git import Shell
from briar.serve.state import WorkStatus
from briar.serve.work import BudgetReopen
from tests.unit.serve.test_review import Reviews
from tests.unit.serve.ticket_fakes import Entries, FakeGithubSide, Files, Items, Plans, ScriptedAgent, Setup, Setups, Step

PR = FakeGithubSide.NUMBER
BRANCH = "briar/ENG-1"
# ENG-1 had spent 4.6 of 5.0; the build and the self review cost 0.3 each, and the cap after the raise.
ALMOST_ALL = 4.6
THIRD_OF_LEFT = 0.3
RAISED_USD = 8.0


class Origin:
    def head(self, setup: Setup) -> str:
        return Shell().line(f"git rev-parse {BRANCH}", setup.origin.root / "remotes" / "acme" / "app.git").output.strip()


class Stopped:
    """A ticket that ran out of money: a draft PR, and `max_usd` raised since."""

    def raised(self, setups: Setups) -> Setup:
        setup = setups.make({})
        setup.save(Entries().planned(spent=ALMOST_ALL))
        review = replace(Plans().approved(), cost=THIRD_OF_LEFT)
        setup.work_on(ScriptedAgent([Step(writes=Files().feature(), cost=THIRD_OF_LEFT), review]), Items().jira())
        return setup.raised(RAISED_USD)


class TestAPrAPersonTook:
    def test_a_draft_of_briar_is_still_finished(self, setups: Setups) -> None:
        """The control: the same stop with the PR still a draft goes on in it (decision B10)."""
        raised = Stopped().raised(setups)
        raised.sources.github_side.branch_pulls[BRANCH] = BranchPull(PR, open=True, merged=False, draft=True)
        asyncio.run(BudgetReopen(raised.company).apply())

        raised.work_on(ScriptedAgent([Plans().approved()]), Items().jira())

        assert (raised.entry("ENG-1").status, raised.sources.github_side.readies) == (WorkStatus.DONE, [PR])

    def test_a_pr_a_person_marked_ready_gets_no_push_no_build_and_is_done(self, setups: Setups) -> None:
        raised = Stopped().raised(setups)
        raised.sources.github_side.branch_pulls[BRANCH] = BranchPull(PR, open=True, merged=False, draft=False)
        asyncio.run(BudgetReopen(raised.company).apply())
        before = Origin().head(raised)
        agent = ScriptedAgent([])

        raised.work_on(agent, Items().jira())

        entry = raised.entry("ENG-1")
        assert (entry.status, entry.money_stop, entry.pr) == (WorkStatus.DONE, None, PR)
        assert (agent.requests, Origin().head(raised), raised.sources.github_side.readies) == ([], before, [])

    def test_the_worktree_is_left_on_the_branch_of_the_pr_with_what_a_reviewer_pushed(self, setups: Setups) -> None:
        """So the next review round starts from the branch of the PR, as for any adopted PR."""
        raised = Stopped().raised(setups)
        raised.sources.github_side.branch_pulls[BRANCH] = BranchPull(PR, open=True, merged=False, draft=False)
        asyncio.run(BudgetReopen(raised.company).apply())
        raised.origin.push_commit(BRANCH, {"from-the-reviewer.txt": "x\n"})

        raised.work_on(ScriptedAgent([]), Items().jira())

        assert (raised.cache / "worktrees" / "acme" / "briar__ENG-1" / "from-the-reviewer.txt").exists()

    def test_review_comments_on_it_are_still_answered(self, setups: Setups) -> None:
        raised = Stopped().raised(setups)
        raised.sources.github_side.branch_pulls[BRANCH] = BranchPull(PR, open=True, merged=False, draft=False)
        asyncio.run(BudgetReopen(raised.company).apply())
        raised.work_on(ScriptedAgent([]), Items().jira())
        reviews = Reviews()
        rows = [{"id": "inline:1", "changed": False, "reply": "Fine as it is."}]
        answer = Step(structured=reviews.answers(rows))

        raised.work_on(ScriptedAgent([answer]), reviews.item([reviews.inline()]))

        assert (raised.entry("ENG-1").rounds, len(raised.sources.github_side.replies)) == (1, 1)
