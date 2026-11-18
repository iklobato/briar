"""`RepoCheckout.pushed` and `sync` (hardening PR, audit findings 1 and 12), with real git against a local origin.
The origin is the truth for the branch of a PR of briar: a commit that only the local branch has is unverified
work of a run that died, and `HEAD` must equal `origin/<branch>` after a sync."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

from briar.serve.git import GitIdentity, Remote, RepoCheckout, Shell
from tests.unit.serve.ticket_fakes import LocalOrigin

TOKEN = "PLACEHOLDER-not-a-secret"
BRANCH = "briar/ENG-1"
DROPPED = "1 commit(s) that are not on the origin"


@dataclass(frozen=True)
class Task:
    """A worktree on `briar/ENG-1` with one commit pushed, and the local origin it was pushed to."""

    origin: LocalOrigin
    checkout: RepoCheckout
    worktree: Path

    def origin_head(self) -> str:
        return Shell().line(f"git rev-parse {BRANCH}", self.origin.root / "remotes" / "acme" / "app.git").output.strip()

    def commit_locally(self, name: str) -> None:
        (self.worktree / name).write_text("local\n")
        self.checkout.commit_all(self.worktree, f"local {name}", GitIdentity("octo"))

    def synced(self) -> str:
        """What a task does at its start: fetch, then sync the worktree to the branch."""
        self.checkout.update(TOKEN)
        self.checkout.sync(self.worktree, BRANCH)
        return self.checkout.head(self.worktree)


class Tasks:
    def pushed(self, tmp_path: Path) -> Task:
        origin = LocalOrigin(tmp_path)
        checkout = RepoCheckout(Shell(), tmp_path / "cache", Remote("acme/app", f"file://{origin.create()}"))
        checkout.update(TOKEN)
        worktree = checkout.worktree(BRANCH, "main", tmp_path / "cache" / "wt")
        (worktree / "pushed.txt").write_text("pushed\n")
        checkout.commit_all(worktree, "pushed", GitIdentity("octo"))
        checkout.push(worktree, BRANCH, TOKEN)
        return Task(origin, checkout, worktree)


class TestPushed:
    def test_the_tracking_ref_moves_with_a_push(self, tmp_path: Path) -> None:
        """All of the pause and stop logic rests on this: no fetch is needed after the push of the task itself."""
        task = Tasks().pushed(tmp_path)

        assert task.checkout.pushed(task.worktree, BRANCH) == task.origin_head()

    def test_a_local_commit_does_not_move_it(self, tmp_path: Path) -> None:
        task = Tasks().pushed(tmp_path)
        before = task.checkout.head(task.worktree)

        task.commit_locally("later.txt")

        assert (task.checkout.pushed(task.worktree, BRANCH), task.checkout.head(task.worktree) != before) == (before, True)


class TestSync:
    def test_behind_is_a_plain_fast_forward_with_no_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger="briar.serve.git")
        task = Tasks().pushed(tmp_path)
        task.origin.push_commit(BRANCH, {"suggested.txt": "from a reviewer\n"})

        head = task.synced()

        assert (head, (task.worktree / "suggested.txt").exists(), DROPPED in caplog.text) == (task.origin_head(), True, False)

    def test_ahead_only_is_reset_to_the_origin_with_a_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """A local commit and an origin that did not move: the fast forward says `Already up to date` and keeps the commit,
        which a later round would take as its start and push without it ever being in a self review diff."""
        caplog.set_level(logging.WARNING, logger="briar.serve.git")
        task = Tasks().pushed(tmp_path)
        task.commit_locally("never-pushed.txt")

        head = task.synced()

        assert (head, (task.worktree / "never-pushed.txt").exists(), DROPPED in caplog.text) == (task.origin_head(), False, True)

    def test_diverged_is_reset_to_the_origin_with_a_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """A local commit that failed verify, then a reviewer pushed on the branch: the fast forward fails, and used to fail
        on every poll for ever."""
        caplog.set_level(logging.WARNING, logger="briar.serve.git")
        task = Tasks().pushed(tmp_path)
        task.commit_locally("never-pushed.txt")
        task.origin.push_commit(BRANCH, {"suggested.txt": "from a reviewer\n"})

        head = task.synced()

        assert (head, (task.worktree / "never-pushed.txt").exists(), (task.worktree / "suggested.txt").exists()) == (task.origin_head(), False, True)
        assert DROPPED in caplog.text

    def test_a_clean_worktree_on_the_origin_head_is_left_alone(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger="briar.serve.git")
        task = Tasks().pushed(tmp_path)

        assert (task.synced(), DROPPED in caplog.text) == (task.origin_head(), False)
