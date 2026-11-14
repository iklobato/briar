"""The ticket routine of `briar serve` (spec 003 PR 2: S18 to S27) end to end, with real
git against a local origin, a scripted agent, and GitHub and Jira fakes that record
what briar writes."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from briar.extract._provider import BranchPull, CiState
from briar.serve.flow import TaskFlow
from briar.serve.state import WorkStatus
from tests.unit.serve.ticket_fakes import ISSUE, BrokenAgent, FakeGithubSide, Files, Items, Plans, ScriptedAgent, Setups, Step

PR = FakeGithubSide.NUMBER
# Verify commands that pass on the base, as a test suite does (a base that fails stops the task).
CLEAN = "! grep -qs bad state.txt"
IGNORED_CHECK = "test ! -e .gitignore || test -f feature.txt"
NO_OTHER = "test ! -e other.txt"
TWO_STEPS = 2
MAX_USD = 5.0
STEP_COST = 0.1
THIRD_STEP, FOURTH_STEP = 2, 3
# The text in a recorded (repo, number, text) comment.
TEXT = 2


class TestHappyPath:
    def test_every_step_of_a_ticket_is_logged(self, setups: Setups, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="briar.serve.flow")
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        lines = [record.getMessage().strip() for record in caplog.records if " is " in record.getMessage()]
        link = f"https://github.com/acme/app/pull/{PR}"
        assert [line.split(" US$")[0] for line in lines] == ["serve: ENG-1 is planned", f"serve: ENG-1 is pr-open {link}", f"serve: ENG-1 is done {link}"]

    def test_the_verify_results_and_the_cost_of_a_done_task_are_logged(self, setups: Setups, caplog: pytest.LogCaptureFixture) -> None:
        """Real run, 2026-11-12: nothing in the log said whether verify ran, nor what a done task cost."""
        caplog.set_level(logging.INFO, logger="briar.serve.flow")
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        lines = [record.getMessage().strip() for record in caplog.records]
        assert "serve: ENG-1 verify on HEAD: `test -f feature.txt` ok" in lines
        assert lines[-1].startswith(f"serve: ENG-1 is done https://github.com/acme/app/pull/{PR} US$ ") and lines[-1].endswith("fix loops")

    def test_a_jira_ticket_goes_from_plan_to_green_ci(self, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        tracker, github = setup.sources.tracker_side, setup.sources.github_side
        assert [key for key, _ in tracker.comments] == ["ENG-1", "ENG-1"]
        assert tracker.comments[0][1].startswith("briar plan for ENG-1:\n\nAdd the feature file\n\nSteps:\n1. write feature.txt")
        assert tracker.comments[1][1] == f"briar opened https://github.com/acme/app/pull/{PR}"
        assert tracker.moves == [("ENG-1", "In Progress"), ("ENG-1", "In Review")]
        repo, draft = github.pulls[0]
        assert (repo, draft.head, draft.base, draft.title, draft.draft) == ("acme/app", "briar/ENG-1", "main", "ENG-1: Add the feature", True)
        assert github.readies == [PR]
        assert "Ticket: ENG-1" in draft.body
        assert "briar/ENG-1" in setup.origin.branches()
        entry = setup.entry("ENG-1")
        assert (entry.status, entry.pr, entry.branch) == (WorkStatus.DONE, PR, "briar/ENG-1")

    def test_the_agent_gets_the_worktree_the_budget_and_read_only_steps(self, setups: Setups, tmp_path: Path) -> None:
        setup = setups.make({})
        knowledge = setup.company.knowledge_file()
        knowledge.parent.mkdir(parents=True, exist_ok=True)
        knowledge.write_text("# acme\n")
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()])

        setup.work_on(agent, Items().jira())

        plan, build, review = agent.requests
        assert [request.access.value for request in agent.requests] == ["read", "edit", "read"]
        assert plan.cwd == tmp_path / "cache" / "worktrees" / "acme" / "briar__ENG-1"
        assert (plan.max_usd, build.max_usd) == (MAX_USD, pytest.approx(MAX_USD - STEP_COST))
        assert str(setup.company.knowledge_file()) in plan.system_prompt
        assert "We need feature.txt" in plan.prompt
        assert "+done" in review.prompt

    def test_a_github_issue_is_closed_by_the_pr_and_not_moved(self, setups: Setups) -> None:
        setup = setups.make({"repos": ["acme/app"]})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().issue())

        github = setup.sources.github_side
        assert github.pulls[0][1].head == f"briar/issue-{ISSUE}"
        assert f"Closes #{ISSUE}" in github.pulls[0][1].body
        assert [(repo, number) for repo, number, _ in github.comments] == [("acme/app", ISSUE), ("acme/app", ISSUE)]
        assert setup.sources.tracker_side.moves == []


class TestNothingToChange:
    """Real run, 2026-11-13: an operations ticket (copy a dataset) needed no code change; the
    agent said so and changed nothing, and briar still pushed the base and opened an empty MR."""

    def test_a_build_with_no_change_opens_no_pr_and_reports_on_the_ticket(self, setups: Setups) -> None:
        setup = setups.make({"verify": [CLEAN]})
        agent = ScriptedAgent([Plans().plan(), Step()])

        setup.work_on(agent, Items().jira())

        tracker, github = setup.sources.tracker_side, setup.sources.github_side
        assert (github.pulls, len(agent.requests), setup.entry("ENG-1").status) == ([], TWO_STEPS, WorkStatus.BLOCKED)
        assert "briar/ENG-1" not in setup.origin.branches()
        assert tracker.comments[-1][1].startswith("briar stopped: the plan needs no change of the code in acme/app")
        assert tracker.moves == [("ENG-1", "In Progress")]


class TestVerifyPerRepo:
    """Decision C1 of the autonomous serve: one company has Python and Terraform repos, so the
    runbook maps each repo to its own verify commands; `verify` stays the default."""

    def test_the_commands_of_the_repo_of_the_task_run(self, setups: Setups, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="briar.serve.flow")
        setup = setups.make({"verify": ["false"], "verify_by_repo": {"acme/app": [CLEAN], "acme/api": ["false"]}})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        assert setup.entry("ENG-1").status is WorkStatus.DONE
        assert f"serve: ENG-1 verify on HEAD: `{CLEAN}` ok" in caplog.text

    def test_a_repo_with_no_commands_and_no_default_relies_on_ci_only(self, setups: Setups, caplog: pytest.LogCaptureFixture) -> None:
        """Decision C3: no local verify; the MR CI is the only check."""
        caplog.set_level(logging.INFO, logger="briar.serve.flow")
        setup = setups.make({"verify": [], "verify_by_repo": {"acme/api": ["false"]}})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        assert setup.entry("ENG-1").status is WorkStatus.DONE
        assert "serve: ENG-1 verify on HEAD: no verify commands set" in caplog.text


class TestUsualReviewers:
    """Decision D2 of the autonomous serve: a ready MR asks the usual reviewers of its repo,
    so it does not wait for the maintainer."""

    def test_a_ready_pr_asks_the_two_usual_reviewers_of_the_repo(self, setups: Setups) -> None:
        setup = setups.make({})
        setup.sources.github_side.usual["acme/app"] = ["ann", "bob", "cy"]

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        assert setup.sources.github_side.asked == [(PR, ["ann", "bob"])]

    def test_a_repo_with_no_review_history_asks_nobody_and_says_so(self, setups: Setups, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="briar.serve.ticket")
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        assert setup.sources.github_side.asked == []
        assert "serve: ENG-1: no usual reviewers found for acme/app; nobody was asked to review" in caplog.text


class TestSilentPlan:
    """Decision D1 of the autonomous serve: the plan comes first and writes nothing; only a
    plan with a code change is posted, moves the ticket and builds. A real run (2026-11-13)
    posted a plan and opened an empty MR for an operations ticket."""

    def test_a_plan_with_no_code_change_writes_nothing_anywhere(self, setups: Setups) -> None:
        setup = setups.make({})
        agent = ScriptedAgent([Plans().no_code()])

        setup.work_on(agent, Items().jira())

        tracker, github = setup.sources.tracker_side, setup.sources.github_side
        assert (tracker.comments, tracker.moves, github.pulls, len(agent.requests)) == ([], [], [], 1)
        assert "briar/ENG-1" not in setup.origin.branches()
        entry = setup.entry("ENG-1")
        assert (entry.status, entry.plan.startswith("Copy the dataset to both clusters")) == (WorkStatus.BLOCKED, True)
        assert entry.report == "the plan needs no code change in acme/app; nothing was posted on the ticket"

    def test_a_plan_with_a_code_change_is_posted_before_the_build(self, setups: Setups) -> None:
        setup = setups.make({})
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()])

        setup.work_on(agent, Items().jira())

        assert setup.sources.tracker_side.moves[0] == ("ENG-1", "In Progress")
        assert setup.entry("ENG-1").status is WorkStatus.DONE


class TestNoSilencedSecretScanner:
    """Real run, 2026-11-13: a secret scan failed on an MR that re-flowed two lines holding
    plain database passwords; to pass CI the agent wrote `.gitleaksignore` with their
    fingerprints, and briar pushed it and marked the MR ready."""

    def test_an_ignore_file_of_a_secret_scanner_is_never_pushed(self, setups: Setups) -> None:
        setup = setups.make({"verify": [CLEAN]})
        agent = ScriptedAgent([Plans().plan(), Step(writes={**Files().feature(), ".gitleaksignore": "abc:app.tfvars:generic-api-key:13\n"})])

        setup.work_on(agent, Items().jira())

        report = setup.sources.tracker_side.comments[-1][1]
        assert (setup.entry("ENG-1").status, len(agent.requests)) == (WorkStatus.BLOCKED, TWO_STEPS)
        assert report.startswith("briar stopped: the change silences a secret scanner (.gitleaksignore)")
        assert ".gitleaksignore" not in setup.origin.files("briar/ENG-1")

    def test_an_allow_comment_of_a_secret_scanner_is_never_pushed(self, setups: Setups) -> None:
        setup = setups.make({"verify": [CLEAN]})
        agent = ScriptedAgent([Plans().plan(), Step(writes={"app.tfvars": 'password = "x" # gitleaks:allow\n'})])

        setup.work_on(agent, Items().jira())

        assert setup.sources.tracker_side.comments[-1][1].startswith("briar stopped: the change silences a secret scanner (app.tfvars)")
        assert "app.tfvars" not in setup.origin.files("briar/ENG-1")


class TestLoops:
    def test_a_failed_verify_is_fixed_by_the_agent(self, setups: Setups) -> None:
        # A verify that passes on the base, as a test suite does, and fails on a bad change.
        setup = setups.make({"verify": [CLEAN]})
        agent = ScriptedAgent([Plans().plan(), Step(writes={"state.txt": "bad\n"}), Step(writes={"state.txt": "good\n"}), Plans().approved()])

        setup.work_on(agent, Items().jira())

        assert f"`{CLEAN}` failed (exit 1)" in agent.requests[THIRD_STEP].prompt
        assert setup.entry("ENG-1").status is WorkStatus.DONE

    def test_a_verify_that_fails_on_the_base_too_stops_without_a_fix(self, setups: Setups) -> None:
        """Real run, 2026-11-12: a pytest plugin of the machine failed verify; the agent then
        changed the config and a test of the repo to pass it. Now briar stops with the report."""
        setup = setups.make({"verify": ["false"]})
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature())])

        setup.work_on(agent, Items().jira())

        assert (len(agent.requests), setup.entry("ENG-1").status) == (TWO_STEPS, WorkStatus.BLOCKED)
        assert setup.sources.github_side.pulls[0][1].body.startswith("briar stopped: the verify commands fail on origin/main too, before any change")

    def test_verify_checks_the_commit_not_the_files_git_ignores(self, setups: Setups) -> None:
        """A file the agent leaves that git ignores passes verify in its folder but is not
        pushed (first real run: a `pytest.ini` the global gitignore dropped)."""
        setup = setups.make({"verify": [IGNORED_CHECK]})
        ignored_build = Step(writes={"feature.txt": "done\n", ".gitignore": "feature.txt\n"})
        agent = ScriptedAgent([Plans().plan(), ignored_build, Step(writes={".gitignore": ""}), Plans().approved()])

        setup.work_on(agent, Items().jira())

        fix = agent.requests[THIRD_STEP].prompt
        assert f"`{IGNORED_CHECK}` failed (exit 1)" in fix
        assert "git ignores these files of your folder, so they are not in the commit: feature.txt" in fix
        assert setup.entry("ENG-1").status is WorkStatus.DONE

    def test_a_self_review_finding_is_fixed(self, setups: Setups) -> None:
        setup = setups.make({})
        fix = Step(writes={"feature.txt": "done, see ENG-1\n"})
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().rejected("feature.txt: say why"), fix, Plans().approved()])

        setup.work_on(agent, Items().jira())

        assert "- feature.txt: say why" in agent.requests[FOURTH_STEP].prompt
        assert setup.entry("ENG-1").status is WorkStatus.DONE

    def test_a_later_self_review_sees_what_the_earlier_ones_asked(self, setups: Setups) -> None:
        """Real run, 2026-11-12: three self-reviews asked to add a change log line, then to drop
        it, then to add it again, and the task stopped at max_loops."""
        setup = setups.make({})
        fix = Step(writes={"feature.txt": "done, see ENG-1\n"})
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().rejected("feature.txt: say why"), fix, Plans().approved()])

        setup.work_on(agent, Items().jira())

        second_review = agent.requests[FOURTH_STEP + 1].prompt
        assert "Earlier rounds of this review asked for:\n- feature.txt: say why" in second_review

    def test_a_failed_ci_is_fixed_and_pushed(self, setups: Setups) -> None:
        setup = setups.make({})
        setup.sources.github_side.ci[:] = [CiState.FAILED, CiState.PASSED]
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), Step(writes={"more.txt": "ci\n"})])

        setup.work_on(agent, Items().jira())

        assert "1 failed: test_feature" in agent.requests[FOURTH_STEP].prompt
        # A CI check may name the PR (the click copy asked for {pr}`4`): the agent must know it.
        assert f"CI fails on the pull request #{PR} (https://github.com/acme/app/pull/{PR})" in agent.requests[FOURTH_STEP].prompt
        assert setup.entry("ENG-1").status is WorkStatus.DONE
        assert setup.sources.github_side.readies == [PR]


class TestLimits:
    """S23: on a limit, push, a draft PR with the report, the report on the ticket."""

    def test_a_self_review_that_never_approves_says_what_it_still_asks(self, setups: Setups) -> None:
        """Real run, 2026-11-12: the report said only "max_loops reached while fixing: the self-review"."""
        setup = setups.make({"max_loops": 1})
        steps = [Plans().plan(), Step(writes=Files().feature()), Plans().rejected("a.py: add a test"), Step(), Plans().rejected("a.py: add a test")]

        setup.work_on(ScriptedAgent(steps), Items().jira())

        assert setup.sources.github_side.pulls[0][1].body.endswith("The self-review still asks for:\n- a.py: add a test")

    def test_verify_that_never_passes_hits_max_loops(self, setups: Setups) -> None:
        setup = setups.make({"max_loops": 1, "verify": [NO_OTHER]})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes={"other.txt": "x"}), Step(writes={"other.txt": "y"})]), Items().jira())

        github, tracker = setup.sources.github_side, setup.sources.tracker_side
        assert github.pulls[0][1].draft is True
        expected = "briar stopped: max_loops (1) reached while fixing: the verify commands. Spent US$ 0.30 of 5.0, 2 of 1 fix loops."
        assert (github.pulls[0][1].body, tracker.comments[-1][1]) == (expected, expected)
        assert setup.entry("ENG-1").status is WorkStatus.BLOCKED

    def test_ci_that_never_ends_hits_the_ci_wait(self, setups: Setups, monkeypatch: pytest.MonkeyPatch) -> None:
        setup = setups.make({})
        monkeypatch.setattr(TaskFlow, "SECONDS_PER_MINUTE", 0)
        setup.sources.github_side.ci[:] = [CiState.PENDING]

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        github = setup.sources.github_side
        assert (github.drafts, [repo for repo, _ in github.pulls]) == ([PR], ["acme/app"])
        assert github.readies == []
        assert github.comments[-1][TEXT].startswith("briar stopped: CI did not pass within ci_wait_minutes (30)")
        assert setup.entry("ENG-1").status is WorkStatus.BLOCKED

    def test_a_plan_that_spends_the_budget_stops_with_no_pr(self, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Step(cost=MAX_USD)]), Items().jira())

        assert setup.sources.github_side.pulls == []
        assert setup.sources.tracker_side.comments[-1][1].startswith("briar stopped: the task spent its max_usd of 5.0")
        entry = setup.entry("ENG-1")
        assert (entry.status, entry.pr) == (WorkStatus.BLOCKED, 0)

    def test_a_refused_pull_request_blocks_the_ticket_for_good(self, setups: Setups) -> None:
        """A GitHub refusal is a report, not a crash: a crash left the ticket `planned`, so
        every cycle planned, built and paid for it again."""
        setup = setups.make({})
        setup.sources.github_side.refusals.append("HTTP 422: A pull request already exists")

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        entry = setup.entry("ENG-1")
        report = "briar stopped: HTTP 422: A pull request already exists. Spent US$ 0.30 of 5.0, 0 of 3 fix loops."
        assert (entry.status, entry.taken(), entry.pr) == (WorkStatus.BLOCKED, True, 0)
        assert entry.report == f"{report} The draft PR failed: HTTP 422: A pull request already exists."
        assert setup.sources.tracker_side.comments[-1][1] == entry.report


    def test_a_refused_push_reports_without_the_local_folder(self, setups: Setups, tmp_path: Path) -> None:
        """Real run, 2026-11-13: a person had pushed to the task branch; the report on the
        public issue held the local worktree path of the machine."""
        setup = setups.make({})
        setup.origin.branch("briar/ENG-1")
        setup.origin.push_commit("briar/ENG-1", {"other.txt": "someone else\n"})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        report = setup.sources.tracker_side.comments[-1][1]
        assert ("non-fast-forward" in report, str(tmp_path / "cache") in report) == (True, False)
        assert setup.entry("ENG-1").status is WorkStatus.BLOCKED


class TestNoDoubleWork:
    """Found in the validation run of 2026-11-11: work done twice after a stop or a lost state."""

    def test_a_failure_that_is_not_a_cli_error_blocks_the_ticket(self, setups: Setups) -> None:
        """Left `planned`, the ticket would be planned, posted and paid for again every poll."""
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Plans().plan()]), Items().jira())

        entry = setup.entry("ENG-1")
        assert (entry.status, entry.taken()) == (WorkStatus.BLOCKED, True)
        assert entry.report.startswith("briar stopped: pop from empty list.")

    def test_any_unexpected_failure_blocks_the_ticket(self, setups: Setups) -> None:
        """Real run, 2026-11-13: a library `TypeError` escaped the task, which stayed taken
        nowhere and was planned and paid for again every poll (decision F2)."""
        setup = setups.make({})

        setup.work_on(BrokenAgent([Plans().plan()]), Items().jira())

        entry = setup.entry("ENG-1")
        assert (entry.status, entry.taken()) == (WorkStatus.BLOCKED, True)
        assert entry.report.startswith("briar stopped: TypeError: 'NoneType' object is not subscriptable.")

    def test_a_refused_ticket_comment_after_the_pr_keeps_the_pr(self, setups: Setups) -> None:
        setup = setups.make({})
        setup.sources.tracker_side.refused_prefixes.append("briar opened")

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        entry = setup.entry("ENG-1")
        assert (entry.status, entry.pr, len(setup.sources.github_side.pulls)) == (WorkStatus.BLOCKED, PR, 1)

    def test_a_ticket_planned_before_a_stop_is_not_announced_twice(self, setups: Setups) -> None:
        setup = setups.make({})
        first = ScriptedAgent([Plans().plan(), Step(cost=MAX_USD)])
        setup.work_on(first, Items().jira())
        planned = [text for _, text in setup.sources.tracker_side.comments if text.startswith("briar plan for")]
        asyncio.run(setup.company.state.record(setup.entry("ENG-1").model_copy(update={"status": WorkStatus.PLANNED})))

        setup.work_on(ScriptedAgent([Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        again =[text for _, text in setup.sources.tracker_side.comments if text.startswith("briar plan for")]
        assert (len(planned), len(again)) == (1, 1)

    def test_a_ticket_planned_before_a_stop_builds_the_posted_plan_without_a_new_one(self, setups: Setups) -> None:
        """Real run, 2026-11-13 (test plan A6): serve was killed after the plan; on restart it
        asked the agent for a new plan (paid twice) and built that one, not the plan on the ticket."""
        setup = setups.make({})
        setup.work_on(ScriptedAgent([Plans().plan(), Step(cost=MAX_USD)]), Items().jira())
        asyncio.run(setup.company.state.record(setup.entry("ENG-1").model_copy(update={"status": WorkStatus.PLANNED})))
        second = ScriptedAgent([Step(writes=Files().feature()), Plans().approved()])

        setup.work_on(second, Items().jira())

        build = second.requests[0]
        assert (build.access.value, setup.entry("ENG-1").status) == ("edit", WorkStatus.DONE)
        assert "Add the feature file" in build.prompt

    @pytest.mark.parametrize(
        "found, status",
        [
            (BranchPull(PR, open=True, merged=False, draft=False), WorkStatus.PR_OPEN),
            (BranchPull(PR, open=True, merged=False, draft=True), WorkStatus.BLOCKED),
            (BranchPull(PR, open=False, merged=False, draft=False), WorkStatus.CLOSED),
            (BranchPull(PR, open=False, merged=True, draft=False), WorkStatus.MERGED),
        ],
        ids=["open", "draft", "closed by a person", "merged"],
    )
    def test_a_pr_of_the_branch_is_recorded_and_nothing_is_built(self, setups: Setups, found: BranchPull, status: WorkStatus) -> None:
        setup = setups.make({})
        setup.origin.branch("briar/ENG-1")
        setup.sources.github_side.branch_pulls["briar/ENG-1"] = found
        agent = ScriptedAgent([])

        setup.work_on(agent, Items().jira())

        entry = setup.entry("ENG-1")
        assert (entry.status, entry.pr, agent.requests, setup.sources.github_side.pulls) == (status, PR, [], [])

    def test_an_adopted_pr_leaves_the_worktree_on_its_branch(self, setups: Setups) -> None:
        """Real run, 2026-11-12, lost state: the worktree stayed on the base, so the next
        review round failed with `git merge --ff-only origin/briar/issue-1` (diverging branches)."""
        setup = setups.make({})
        setup.origin.branch("briar/ENG-1")
        setup.origin.push_commit("briar/ENG-1", {"feature.txt": "done\n"})
        setup.origin.push_commit("main", {"other.txt": "moved on\n"})
        setup.sources.github_side.branch_pulls["briar/ENG-1"] = BranchPull(PR, open=True, merged=False, draft=False)

        setup.work_on(ScriptedAgent([]), Items().jira())

        worktree = setup.cache / "worktrees" / "acme" / "briar__ENG-1"
        assert ((worktree / "feature.txt").exists(), (worktree / "other.txt").exists()) == (True, False)

    def test_a_draft_left_during_the_ci_wait_is_finished(self, setups: Setups) -> None:
        setup = setups.make({})
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())
        github = setup.sources.github_side
        github.readies.clear()
        asyncio.run(setup.company.state.record(setup.entry("ENG-1").model_copy(update={"status": WorkStatus.PR_OPEN})))

        setup.work_on(ScriptedAgent([]), Items().ready(PR))

        assert (github.readies, setup.entry("ENG-1").status) == ([PR], WorkStatus.DONE)
        assert "briar/ENG-1" in setup.origin.branches()
