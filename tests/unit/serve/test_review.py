"""The review routine of `briar serve` (spec 003 PR 3: V2, V6 to V11) end to end, with
real git against a local origin, a scripted agent, and GitHub and Jira fakes that record
what briar writes. Each test starts after the ticket routine opened PR 41 on
`briar/ENG-1`."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import pytest

from briar.extract._provider import CiState, CommentKind, ReviewComment, ThreadReply
from briar.serve.flow import TaskFlow
from briar.serve.git import Shell
from briar.serve.state import WorkSource, WorkStatus
from briar.serve.task import Answers
from briar.serve.work import Marker, WorkItem, WorkKind
from tests.unit.serve.ticket_fakes import FakeGithubSide, Files, Items, Plans, ScriptedAgent, Setup, Setups, Step

PR = FakeGithubSide.NUMBER
SHORT_SHA = 7
LAST_TWO = 2
# The text in a recorded (repo, number, text) comment.
TEXT = 2


class Reviews:
    """The review comments and the round items of the tests."""

    def inline(self) -> ReviewComment:
        return ReviewComment(
            id="1", author="ann", body="say why in feature.txt", file_path="feature.txt", line=1,
            kind=CommentKind.INLINE, association="MEMBER", updated_at="t1", thread="1",
        )  # fmt: skip

    def talk(self) -> ReviewComment:
        return ReviewComment(id="2", author="bob", body="please add a test\nsecond line", kind=CommentKind.CONVERSATION, association="OWNER", updated_at="t2")

    def item(self, comments: Sequence[ReviewComment]) -> WorkItem:
        return WorkItem(WorkKind.REVIEW, "ENG-1", WorkSource.JIRA, "acme/app", "Add the feature", pr=PR, comments=tuple(comments))

    def conflicted(self) -> WorkItem:
        return WorkItem(WorkKind.REVIEW, "ENG-1", WorkSource.JIRA, "acme/app", "Add the feature", pr=PR, conflict=True)

    def answers(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"answers": rows}


class Opened:
    """A setup whose ticket ENG-1 went through the ticket routine: PR 41 open on `briar/ENG-1`."""

    def __init__(self, setups: Setups) -> None:
        self._setups = setups

    def make(self, extra: Dict[str, Any]) -> Setup:
        setup = self._setups.make(extra)
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())
        return setup

    def head(self, setup: Setup) -> str:
        return Shell().line("git rev-parse briar/ENG-1", setup.origin.root / "remotes" / "acme" / "app.git").output.strip()


@pytest.fixture
def opened(setups: Setups) -> Opened:
    return Opened(setups)


class TestRound:
    def test_a_round_fixes_pushes_replies_and_records(self, opened: Opened) -> None:
        setup = opened.make({})
        reviews = Reviews()
        rows = [{"id": "inline:1", "changed": True, "reply": "Added the ticket key."}, {"id": "conversation:2", "changed": False, "reply": "verify covers it."}]
        agent = ScriptedAgent([Step(writes={"feature.txt": "done, see ENG-1\n"}, structured=reviews.answers(rows)), Plans().approved()])

        setup.work_on(agent, reviews.item([reviews.inline(), reviews.talk()]))

        sha = opened.head(setup)[:SHORT_SHA]
        github = setup.sources.github_side
        assert github.replies == [ThreadReply(PR, "1", f"Added the ticket key.\n\nChanged in {sha}.\n\n{Marker.TEXT}")]
        assert github.comments[-1] == ("acme/app", PR, f"bob wrote:\n> please add a test\n> second line\n\nverify covers it.\n\n{Marker.TEXT}")
        assert [request.access.value for request in agent.requests] == ["edit", "read"]
        assert setup.origin.log("briar/ENG-1")[0].endswith("|ENG-1: answer the review")
        entry = setup.entry("ENG-1")
        assert (entry.status, entry.rounds, entry.answered) == (WorkStatus.DONE, 1, {"inline:1": "t1", "conversation:2": "t2"})

    def test_the_agent_gets_every_comment_with_its_id(self, opened: Opened) -> None:
        setup = opened.make({})
        reviews = Reviews()
        agent = ScriptedAgent([Step(structured=reviews.answers([]))])

        setup.work_on(agent, reviews.item([reviews.inline(), reviews.talk()]))

        prompt = agent.requests[0].prompt
        assert "id `inline:1`, inline comment by ann on feature.txt:1:\n> say why in feature.txt" in prompt
        assert "id `conversation:2`, conversation comment by bob:\n> please add a test\n> second line" in prompt

    def test_a_round_with_no_change_only_replies(self, opened: Opened) -> None:
        setup = opened.make({})
        before = setup.origin.log("briar/ENG-1")
        reviews = Reviews()
        agent = ScriptedAgent([Step(structured=reviews.answers([{"id": "inline:1", "changed": False, "reply": "The name is the one of the ticket."}]))])

        setup.work_on(agent, reviews.item([reviews.inline()]))

        assert (len(agent.requests), setup.origin.log("briar/ENG-1")) == (1, before)
        assert setup.sources.github_side.replies == [ThreadReply(PR, "1", f"The name is the one of the ticket.\n\n{Marker.TEXT}")]
        assert setup.entry("ENG-1").status is WorkStatus.DONE

    def test_a_comment_with_no_answer_still_gets_a_reply(self, opened: Opened) -> None:
        setup = opened.make({})
        reviews = Reviews()

        setup.work_on(ScriptedAgent([Step(structured=reviews.answers([]))]), reviews.item([reviews.inline()]))

        assert setup.sources.github_side.replies == [ThreadReply(PR, "1", f"{Answers.MISSING.reply}\n\n{Marker.TEXT}")]

    def test_a_commit_of_a_reviewer_is_pulled_in_first(self, opened: Opened) -> None:
        """A suggestion committed on GitHub: without the fast forward the push would be refused."""
        setup = opened.make({})
        setup.origin.push_commit("briar/ENG-1", {"suggested.txt": "from the reviewer\n"})
        reviews = Reviews()
        rows = [{"id": "inline:1", "changed": True, "reply": "Done."}]
        agent = ScriptedAgent([Step(writes={"feature.txt": "done, see ENG-1\n"}, structured=reviews.answers(rows)), Plans().approved()])

        setup.work_on(agent, reviews.item([reviews.inline()]))

        subjects = [line.split("|", 1)[1] for line in setup.origin.log("briar/ENG-1")[:LAST_TWO]]
        assert (subjects, setup.entry("ENG-1").status) == (["ENG-1: answer the review", "other"], WorkStatus.DONE)


class TestConflict:
    """V6: a PR in conflict with its base gets the base merged, the conflicts resolved by
    the agent, and a note."""

    def test_the_base_is_merged_and_the_conflict_resolved(self, opened: Opened) -> None:
        setup = opened.make({})
        setup.origin.push_commit("main", {"feature.txt": "other\n"})
        agent = ScriptedAgent([Step(writes={"feature.txt": "done\nother\n"}), Plans().approved()])

        setup.work_on(agent, Reviews().conflicted())

        parents = setup.origin.log("briar/ENG-1")[0].split("|", 1)[0].split()
        assert len(parents) == len(["branch", "base"])
        assert "merging origin/main into the branch left conflicts" in agent.requests[0].prompt
        assert "- feature.txt" in agent.requests[0].prompt
        sha = opened.head(setup)[:SHORT_SHA]
        note = f"briar merged origin/main into this branch, resolving the conflicts in feature.txt ({sha}).\n\n{Marker.TEXT}"
        assert setup.sources.github_side.comments[-1] == ("acme/app", PR, note)

    def test_markers_left_drop_the_merge_and_block_the_pr(self, opened: Opened) -> None:
        setup = opened.make({})
        before = setup.origin.log("briar/ENG-1")
        setup.origin.push_commit("main", {"feature.txt": "other\n"})

        setup.work_on(ScriptedAgent([Step()]), Reviews().conflicted())

        github = setup.sources.github_side
        assert (github.drafts, setup.origin.log("briar/ENG-1")) == ([PR], before)
        assert github.comments[-1][TEXT].startswith("briar stopped: conflict markers left in feature.txt after merging origin/main.")
        assert setup.entry("ENG-1").status is WorkStatus.BLOCKED


class TestLimits:
    def test_replies_are_recorded_before_the_ci_wait(self, opened: Opened, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stop during the CI wait must not make the next poll answer the same comments."""
        setup = opened.make({})
        monkeypatch.setattr(TaskFlow, "SECONDS_PER_MINUTE", 0)
        setup.sources.github_side.ci[:] = [CiState.PENDING]
        reviews = Reviews()
        rows = [{"id": "inline:1", "changed": True, "reply": "Added the ticket key."}]
        agent = ScriptedAgent([Step(writes={"feature.txt": "done, see ENG-1\n"}, structured=reviews.answers(rows)), Plans().approved()])

        setup.work_on(agent, reviews.item([reviews.inline()]))

        entry = setup.entry("ENG-1")
        assert (entry.status, entry.answered) == (WorkStatus.BLOCKED, {"inline:1": "t1"})

    def test_the_round_limit_stops_with_a_report(self, opened: Opened) -> None:
        """V11: a bot that comments on every push cannot make rounds without end."""
        setup = opened.make({"max_review_rounds": 1})
        reviews = Reviews()
        setup.work_on(ScriptedAgent([Step(structured=reviews.answers([]))]), reviews.item([reviews.inline()]))
        agent = ScriptedAgent([])

        setup.work_on(agent, reviews.item([reviews.talk()]))

        github, entry = setup.sources.github_side, setup.entry("ENG-1")
        assert (agent.requests, github.drafts, entry.status) == ([], [PR], WorkStatus.BLOCKED)
        assert entry.report.startswith(f"briar stopped: max_review_rounds (1) reached on PR #{PR}.")
        assert setup.sources.tracker_side.comments[-1][1] == entry.report


class TestEnded:
    @pytest.mark.parametrize("kind, status", [(WorkKind.MERGED, WorkStatus.MERGED), (WorkKind.CLOSED, WorkStatus.CLOSED)])
    def test_a_pr_that_ended_is_recorded_and_left(self, opened: Opened, kind: WorkKind, status: WorkStatus) -> None:
        setup = opened.make({})
        agent = ScriptedAgent([])

        setup.work_on(agent, WorkItem(kind, "ENG-1", WorkSource.JIRA, "acme/app", "Add the feature", pr=PR))

        entry = setup.entry("ENG-1")
        assert (agent.requests, entry.status, entry.pr, entry.taken(), entry.watched()) == ([], status, PR, True, False)
