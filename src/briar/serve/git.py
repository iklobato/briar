"""Git for `briar serve` (spec 003 PR 2): a clone per repo in a cache folder, a worktree
per task (S15), local commits, and the push, which is briar code and never the agent
(D10, S25).

Commands run with `os.system` and their output goes to a file (maintainer rule: no
`subprocess`). The token never goes on a command line or in the environment: a
credential helper reads it from a file that only the owner can read, removed after the
command."""

from __future__ import annotations

import os
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Sequence

from briar.errors import CliError


@dataclass(frozen=True)
class ShellResult:
    code: int
    output: str

    def ok(self) -> bool:
        return self.code == 0


class Shell:
    """Runs one command line in a folder and returns its exit code and its output."""

    def line(self, command: str, cwd: Path) -> ShellResult:
        """A shell line, as written in the runbook (`verify` commands)."""
        with tempfile.TemporaryDirectory(prefix="briar-shell-") as scratch:
            output = Path(scratch) / "output"
            # The braces send the output of `cd` to the file too, so a missing folder is reported.
            status = os.system(f"{{ cd {shlex.quote(str(cwd))} && ( {command} ); }} > {shlex.quote(str(output))} 2>&1")
            return ShellResult(os.waitstatus_to_exitcode(status), output.read_text(errors="replace"))

    def program(self, argv: Sequence[str], cwd: Path) -> ShellResult:
        """One program with its arguments, each one quoted."""
        return self.line(shlex.join(argv), cwd)


class GitFailed(CliError):
    """A git command briar runs failed; the message holds its output."""


@dataclass(frozen=True)
class GitIdentity:
    """Who the commits are from: the owner of the GitHub token (S2)."""

    login: str

    def args(self) -> Sequence[str]:
        return ("-c", f"user.name={self.login}", "-c", f"user.email={self.login}@users.noreply.github.com")


class RepoCheckout:
    """The clone of one repo in `cache` and the worktrees of its tasks."""

    HOST = "https://github.com"
    # The token file is readable by its owner only.
    SECRET_MODE = 0o600
    # The tail of a failed git output kept in the error, and the arguments named in it.
    OUTPUT_TAIL = 500
    NAMED_ARGS = 3
    HELPER: ClassVar[str] = "!f() {{ echo username=x-access-token; printf 'password='; cat {path}; echo; }}; f"

    def __init__(self, shell: Shell, cache: Path, repo: str) -> None:
        self._shell = shell
        self._cache = cache
        self._repo = repo

    def clone_dir(self) -> Path:
        return self._cache / "repos" / self._repo.replace("/", "__")

    def update(self, token: str) -> None:
        """Clone the repo the first time, then fetch it."""
        if (self.clone_dir() / ".git").exists():
            self._git(["fetch", "origin", "--prune"], self.clone_dir(), token)
            return
        self.clone_dir().parent.mkdir(parents=True, exist_ok=True)
        self._git(["clone", f"{self.HOST}/{self._repo}.git", str(self.clone_dir())], self._cache, token)

    def worktree(self, branch: str, base: str, path: Path) -> Path:
        """The worktree of a task on `branch`, made from `origin/<base>` the first time."""
        if (path / ".git").exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._git(["worktree", "add", "-B", branch, str(path), f"origin/{base}"], self.clone_dir(), "")
        return path

    def commit_all(self, path: Path, message: str, identity: GitIdentity) -> bool:
        """Commit every change of the worktree; False when there was nothing to commit."""
        self._git(["add", "-A"], path, "")
        if self._shell.program(["git", "diff", "--cached", "--quiet"], path).ok():
            return False
        self._git([*identity.args(), "commit", "-m", message], path, "")
        return True

    def diff(self, path: Path, base: str) -> str:
        return self._git(["diff", f"origin/{base}...HEAD"], path, "").output

    def head(self, path: Path) -> str:
        return self._git(["rev-parse", "HEAD"], path, "").output.strip()

    def push(self, path: Path, branch: str, token: str) -> None:
        """Push the branch; never with force (rules no_force_push)."""
        self._git(["push", "origin", f"HEAD:refs/heads/{branch}"], path, token)

    def _git(self, args: Sequence[str], cwd: Path, token: str) -> ShellResult:
        if not token:
            return self._checked(["git", *args], cwd)
        with tempfile.TemporaryDirectory(prefix="briar-git-") as scratch:
            secret = Path(scratch) / "token"
            secret.write_text(token)
            secret.chmod(self.SECRET_MODE)
            helper = self.HELPER.format(path=shlex.quote(str(secret)))
            return self._checked(["git", "-c", "credential.helper=", "-c", f"credential.helper={helper}", *args], cwd)

    def _checked(self, argv: Sequence[str], cwd: Path) -> ShellResult:
        answer = self._shell.program(argv, cwd)
        if not answer.ok():
            named = " ".join(argv[-self.NAMED_ARGS :])
            raise GitFailed(f"`git {named}` failed in {cwd}: {answer.output.strip()[-self.OUTPUT_TAIL :]}")
        return answer


__all__ = ["GitFailed", "GitIdentity", "RepoCheckout", "Shell", "ShellResult"]
