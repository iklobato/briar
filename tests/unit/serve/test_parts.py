"""The parts of the ticket routine (spec 003 PR 2): git with a token that never shows,
the CI state, the plan and the self-review answers, the budget, and the prompt files."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, Dict, List, Optional

import pytest

from briar.extract._provider import CiState
from briar.extract._providers.github import CiStates, GithubProvider
from briar.serve.agent import AgentAnswer
from briar.serve.git import GitFailed, GitIdentity, Remote, RepoCheckout, Shell
from briar.serve.task import Budget, LimitHit, Plan, Prompts, Review
from tests.unit.serve.ticket_fakes import LocalOrigin

TOKEN = "PLACEHOLDER-not-a-secret"
MAX_USD = 1.0
HALF_USD = MAX_USD / 2
EXIT_CODE = 3
PR_NUMBER = 11
# Tasks of one repo that start at the same moment.
TASKS = 4


class TestGit:
    def test_clone_worktree_commit_and_push_with_a_token_that_never_shows(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        origin = LocalOrigin(tmp_path)
        checkout = RepoCheckout(Shell(), tmp_path / "cache", Remote("acme/app", f"file://{origin.create()}"))

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

    def test_two_tasks_of_one_repo_clone_and_add_worktrees_at_once(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Validation run on the click copy: with `max_parallel: 2` the second task failed with
        `destination path ... already exists and is not an empty directory`."""
        remote = Remote("acme/app", f"file://{LocalOrigin(tmp_path).create()}")
        start = threading.Barrier(TASKS)

        def task(name: str) -> Path:
            checkout = RepoCheckout(Shell(), tmp_path / "cache", remote)
            start.wait()
            checkout.update(TOKEN)
            return checkout.worktree(f"briar/{name}", "main", tmp_path / "cache" / name)

        with ThreadPoolExecutor(TASKS) as pool:
            worktrees = list(pool.map(task, [f"ENG-{index}" for index in range(TASKS)]))

        assert [(worktree / "README.md").read_text() for worktree in worktrees] == ["app\n"] * TASKS

    def test_a_base_that_changed_the_same_line_does_not_merge_clean(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """GitHub kept a PR `CLEAN` 40 minutes after its base moved (click copy, PR #5)."""
        checkout = PushedBranch(tmp_path, monkeypatch).after_base_commit({"README.md": "main moved\n"})

        assert checkout.merges_clean("main", "briar/ENG-1", TOKEN) is False

    def test_a_base_that_changed_another_file_merges_clean(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        checkout = PushedBranch(tmp_path, monkeypatch).after_base_commit({"other.txt": "x\n"})

        assert checkout.merges_clean("main", "briar/ENG-1", TOKEN) is True

    def test_a_failed_git_command_names_it_and_hides_the_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        checkout = RepoCheckout(Shell(), tmp_path / "cache", Remote("acme/missing", f"file://{tmp_path / 'remotes'}"))

        with pytest.raises(GitFailed) as caught:
            checkout.update(TOKEN)

        message = str(caught.value)
        command = f"git clone file://{tmp_path}/remotes/acme/missing.git {tmp_path}/cache/repos/acme__missing"
        assert message.startswith(f"`{command}` failed: ")
        assert ("does not appear to be a git repository" in message, TOKEN in message) == (True, False)

    def test_the_shell_gives_the_exit_code_and_the_output(self, tmp_path: Path) -> None:
        done = Shell().line(f"echo out; echo err >&2; exit {EXIT_CODE}", tmp_path)

        assert (done.code, done.output.split()) == (EXIT_CODE, ["out", "err"])

    def test_a_missing_folder_is_a_failed_command_with_the_reason(self, tmp_path: Path) -> None:
        done = Shell().line("echo never", tmp_path / "missing")

        assert (done.ok(), str(tmp_path / "missing") in done.output, "never" in done.output) == (False, True, False)


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

    def test_a_queued_actions_run_keeps_ci_pending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """First real CI wait (PR #11 of the test repo): GitGuardian passed at once, the
        Actions runs were `queued` with no check run yet, and briar marked the PR ready."""
        repo = FakeRepo(check_runs=[FakeRun("completed", "success")], workflow_runs=[FakeRun("queued", None)])
        monkeypatch.setattr(GithubProvider, "_api", lambda provider: FakeApi(repo))

        assert GithubProvider().ci_state("acme/app", PR_NUMBER) is CiState.PENDING
        assert repo.asked_head_shas == [FakeRepo.HEAD]


class PushedBranch:
    """A clone with `briar/ENG-1` pushed (it changes README.md), then a commit on `main`."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._tmp_path = tmp_path
        self._monkeypatch = monkeypatch

    def after_base_commit(self, files: Dict[str, str]) -> RepoCheckout:
        origin = LocalOrigin(self._tmp_path)
        checkout = RepoCheckout(Shell(), self._tmp_path / "cache", Remote("acme/app", f"file://{origin.create()}"))
        checkout.update(TOKEN)
        worktree = checkout.worktree("briar/ENG-1", "main", self._tmp_path / "cache" / "wt")
        (worktree / "README.md").write_text("branch change\n")
        checkout.commit_all(worktree, "ENG-1: change", GitIdentity("octo"))
        checkout.push(worktree, "briar/ENG-1", TOKEN)
        origin.push_commit("main", files)
        return checkout


@dataclass(frozen=True)
class FakeRun:
    status: str
    conclusion: Optional[str]


@dataclass(frozen=True)
class FakeRepo:
    """The PyGithub repository calls of `ci_state`."""

    HEAD: ClassVar[str] = "abc123"
    check_runs: List[FakeRun] = field(default_factory=list)
    workflow_runs: List[FakeRun] = field(default_factory=list)
    asked_head_shas: List[str] = field(default_factory=list)

    def get_pull(self, number: int) -> SimpleNamespace:
        return SimpleNamespace(head=SimpleNamespace(sha=self.HEAD))

    def get_commit(self, sha: str) -> SimpleNamespace:
        return SimpleNamespace(get_check_runs=lambda: self.check_runs, get_combined_status=lambda: SimpleNamespace(statuses=[]))

    def get_workflow_runs(self, head_sha: str) -> List[FakeRun]:
        self.asked_head_shas.append(head_sha)
        return self.workflow_runs


@dataclass(frozen=True)
class FakeApi:
    repo: FakeRepo

    def client(self) -> SimpleNamespace:
        return SimpleNamespace(get_repo=lambda name: self.repo)


class TestAnswers:
    def test_a_plan_from_the_structured_answer(self) -> None:
        plan = Plan.from_answer(AgentAnswer("", {"summary": "Fix login", "steps": ["a", "b"], "risks": ["c"]}, 0.0))

        assert plan.markdown() == "Fix login\n\nSteps:\n1. a\n2. b\n\nRisks:\n1. c"

    def test_a_plan_lists_the_sources_the_agent_read(self) -> None:
        plan = Plan.from_answer(AgentAnswer("", {"summary": "Fix login", "steps": ["a"], "sources": ["Terraform", "Deploy map"]}, 0.0))

        assert plan.markdown() == "Fix login\n\nSteps:\n1. a\n\nSources read:\n1. Terraform\n2. Deploy map"

    def test_the_plan_schema_lets_the_agent_name_its_sources_without_requiring_them(self) -> None:
        assert Plan.SCHEMA["properties"]["sources"] == {"type": "array", "items": {"type": "string"}}
        assert "sources" not in Plan.SCHEMA["required"]

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
    @pytest.mark.parametrize("name", ["system", "plan", "build", "fix", "review", "comments"])
    def test_every_prompt_file_ships_and_fills_in(self, name: str) -> None:
        values = {
            key: f"<{key}>"
            for key in ("company", "repo", "knowledge", "index", "state", "key", "title", "description", "plan", "verify", "what", "details", "diff", "comments", "earlier")
        }

        text = Prompts().text(name, values)

        assert ("{" in text, "}" in text) == (False, False)
        assert ("<key>" in text) is (name != "system")
        assert ("<index>" in text) is (name == "system")
