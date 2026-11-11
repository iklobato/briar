"""The parts of the ticket routine (spec 003 PR 2): git with a token that never shows,
the CI state, the plan and the self-review answers, the budget, and the prompt files."""

from __future__ import annotations

from pathlib import Path

import pytest

from briar.extract._provider import CiState
from briar.extract._providers.github import CiStates
from briar.serve.agent import AgentAnswer
from briar.serve.git import GitFailed, GitIdentity, RepoCheckout, Shell
from briar.serve.task import Budget, LimitHit, Plan, Prompts, Review
from tests.unit.serve.ticket_fakes import LocalOrigin

TOKEN = "PLACEHOLDER-not-a-secret"
MAX_USD = 1.0
HALF_USD = MAX_USD / 2
EXIT_CODE = 3


class TestGit:
    def test_clone_worktree_commit_and_push_with_a_token_that_never_shows(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        origin = LocalOrigin(tmp_path)
        monkeypatch.setattr(RepoCheckout, "HOST", f"file://{origin.create()}")
        checkout = RepoCheckout(Shell(), tmp_path / "cache", "acme/app")

        checkout.update(TOKEN)
        worktree = checkout.worktree("briar/ENG-1", "main", tmp_path / "cache" / "wt")
        (worktree / "feature.txt").write_text("x\n")
        committed = checkout.commit_all(worktree, "ENG-1: add", GitIdentity("octo"))
        again = checkout.commit_all(worktree, "ENG-1: nothing", GitIdentity("octo"))
        checkout.push(worktree, "briar/ENG-1", TOKEN)

        assert (committed, again) == (True, False)
        assert "briar/ENG-1" in origin.branches()
        assert "+x" in checkout.diff(worktree, "main")
        log = Shell().line("git log -1 --format=%an/%ae/%s", worktree).output.strip()
        assert log == "octo/octo@users.noreply.github.com/ENG-1: add"

    def test_a_failed_git_command_names_it_and_hides_the_token(self, tmp_path: Path) -> None:
        checkout = RepoCheckout(Shell(), tmp_path, "acme/missing")

        with pytest.raises(GitFailed) as caught:
            checkout.worktree("b", "main", tmp_path / "wt")

        assert str(caught.value).startswith("`git ")
        assert "No such file or directory" in str(caught.value)
        assert TOKEN not in str(caught.value)

    def test_the_shell_gives_the_exit_code_and_the_output(self, tmp_path: Path) -> None:
        done = Shell().line(f"echo out; echo err >&2; exit {EXIT_CODE}", tmp_path)

        assert (done.code, done.output.split()) == (EXIT_CODE, ["out", "err"])

    def test_a_missing_folder_is_a_failed_command_with_the_reason(self, tmp_path: Path) -> None:
        done = Shell().line("echo never", tmp_path / "missing")

        assert (done.ok(), "No such file or directory" in done.output) == (False, True)


class TestCiStates:
    @pytest.mark.parametrize(
        "runs, statuses, state",
        [
            ([], [], CiState.NONE),
            ([("completed", "success")], ["success"], CiState.PASSED),
            ([("in_progress", None)], [], CiState.PENDING),
            ([("completed", "success")], ["pending"], CiState.PENDING),
            ([("completed", "failure"), ("in_progress", None)], [], CiState.FAILED),
            ([("completed", "success")], ["error"], CiState.FAILED),
        ],
        ids=["none", "passed", "running", "status pending", "a failed run", "a failed status"],
    )
    def test_one_state_from_runs_and_statuses(self, runs: list, statuses: list, state: CiState) -> None:
        assert CiStates().of(runs, statuses) is state


class TestAnswers:
    def test_a_plan_from_the_structured_answer(self) -> None:
        plan = Plan.from_answer(AgentAnswer("", {"summary": "Fix login", "steps": ["a", "b"], "risks": ["c"]}, 0.0))

        assert plan.markdown() == "Fix login\n\nSteps:\n1. a\n2. b\n\nRisks:\n1. c"

    def test_a_plan_with_no_structure_keeps_the_text(self) -> None:
        assert Plan.from_answer(AgentAnswer("do it", {}, 0.0)).markdown() == "do it"

    def test_a_review_with_no_answer_is_not_approved(self) -> None:
        assert (Review.from_answer(AgentAnswer("", {}, 0.0)), Review.from_answer(AgentAnswer("", {"approved": True}, 0.0))) == (
            Review(False),
            Review(True),
        )


class TestBudget:
    def test_spending_past_max_usd_stops(self) -> None:
        budget = Budget(MAX_USD, 1)
        budget.spend(AgentAnswer("", {}, HALF_USD))

        with pytest.raises(LimitHit, match=r"^the task spent its max_usd of 1.0$"):
            budget.spend(AgentAnswer("", {}, HALF_USD))

    def test_the_sdk_budget_stop_stops(self) -> None:
        with pytest.raises(LimitHit):
            Budget(MAX_USD, 1).spend(AgentAnswer("", {}, 0.0, failed=True, reason="error_max_budget_usd"))

    def test_one_loop_too_many_stops(self) -> None:
        budget = Budget(MAX_USD, 1)
        budget.next_loop("verify")

        with pytest.raises(LimitHit, match=r"^max_loops \(1\) reached while fixing: CI$"):
            budget.next_loop("CI")


class TestPrompts:
    @pytest.mark.parametrize("name", ["system", "plan", "build", "fix", "review"])
    def test_every_prompt_file_ships_and_fills_in(self, name: str) -> None:
        values = {
            key: f"<{key}>" for key in ("company", "repo", "knowledge", "state", "key", "title", "description", "plan", "verify", "what", "details", "diff")
        }

        text = Prompts().text(name, values)

        assert ("{" in text, "}" in text) == (False, False)
        assert ("<key>" in text) is (name != "system")
