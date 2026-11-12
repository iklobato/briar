"""Git for `briar serve` (spec 003 PR 2): a clone per repo in a cache folder, a worktree
per task (S15), local commits, the merge of the base into a PR in conflict (PR 3, V6), and
the push, which is briar code and never the agent (D10, S25).

Commands run with `os.system` and their output goes to a file (maintainer rule: no
`subprocess`). The token never goes on a command line or in the environment: a
credential helper reads it from a file that only the owner can read, removed after the
command."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Dict, List, Sequence

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
    """Who the commits are from: the owner of the token (S2). No email: the GitHub noreply one;
    GitLab gives the commit email of the profile (G9)."""

    login: str
    email: str = ""

    def args(self) -> Sequence[str]:
        return ("-c", f"user.name={self.login}", "-c", f"user.email={self.email or f'{self.login}@users.noreply.github.com'}")


@dataclass(frozen=True)
class Remote:
    """Where a repo is cloned from and the git user its token goes with (GitHub
    `x-access-token`, GitLab `oauth2`)."""

    name: str
    base: str = "https://github.com"
    user: str = "x-access-token"

    def address(self) -> str:
        return f"{self.base}/{self.name}.git"


class RepoCheckout:
    """The clone of one repo in `cache` and the worktrees of its tasks."""

    # The token file is readable by its owner only.
    SECRET_MODE = 0o600
    # The tail of a failed git output kept in the error.
    OUTPUT_TAIL = 500
    # A line git leaves in a file with a conflict.
    CONFLICT_MARK: ClassVar["re.Pattern[str]"] = re.compile(r"^(<{7}|>{7})( |$)", re.MULTILINE)
    HELPER: ClassVar[str] = "!f() {{ echo username={user}; printf 'password='; cat {path}; echo; }}; f"
    # The prefix of an ignored file in `git status --porcelain --ignored`.
    IGNORED = "!! "
    # One lock per clone folder, for every task of the process (they run in threads).
    CLONE_LOCKS: ClassVar[Dict[str, threading.Lock]] = {}
    CLONE_LOCKS_GUARD: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, shell: Shell, cache: Path, remote: Remote) -> None:
        self._shell = shell
        self._cache = cache
        self._remote = remote

    def clone_dir(self) -> Path:
        return self._cache / "repos" / self._remote.name.replace("/", "__")

    def update(self, token: str) -> None:
        """Clone the repo the first time, then fetch it."""
        with self._shared_clone():
            if (self.clone_dir() / ".git").exists():
                self._git(["fetch", "origin", "--prune"], self.clone_dir(), token)
                return
            self.clone_dir().parent.mkdir(parents=True, exist_ok=True)
            self._git(["clone", self._remote.address(), str(self.clone_dir())], self._cache, token)

    def worktree(self, branch: str, base: str, path: Path) -> Path:
        """The worktree of a task on `branch`, made from `origin/<base>` the first time."""
        if (path / ".git").exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._shared_clone():
            self._git(["worktree", "add", "-B", branch, str(path), f"origin/{base}"], self.clone_dir(), "")
        return path

    def merges_clean(self, base: str, branch: str, token: str) -> bool:
        """Fetch, then try the merge of `origin/<base>` and `origin/<branch>` without touching
        any worktree (`git merge-tree`): False when they conflict."""
        self.update(token)
        with self._shared_clone():
            done = self._shell.program(["git", "merge-tree", "--write-tree", f"origin/{base}", f"origin/{branch}"], self.clone_dir())
        return done.ok()

    def _shared_clone(self) -> threading.Lock:
        """The lock of the clone folder: the tasks of one repo (`max_parallel`) share the clone,
        and two `git clone`, `fetch` or `worktree add` at once fail or corrupt it."""
        with self.CLONE_LOCKS_GUARD:
            return self.CLONE_LOCKS.setdefault(str(self.clone_dir()), threading.Lock())

    def adopt(self, path: Path, branch: str) -> None:
        """Put a worktree made from the base on `origin/<branch>`: briar found its own PR there
        (a lost state) before it changed anything, and the next review round fast forwards it."""
        self._git(["checkout", "-B", branch, f"origin/{branch}"], path, "")

    def sync(self, path: Path, branch: str) -> None:
        """Bring the worktree to `origin/<branch>` (a reviewer may have pushed a suggestion);
        only a fast forward, so work of briar is never lost."""
        self._git(["merge", "--ff-only", f"origin/{branch}"], path, "")

    def merge(self, path: Path, base: str, identity: GitIdentity) -> List[str]:
        """Merge `origin/<base>` into the worktree (V6): no file when it merged clean, else
        the files in conflict, left with their markers for the agent."""
        done = self._shell.program(["git", *identity.args(), "merge", "--no-edit", f"origin/{base}"], path)
        if done.ok():
            return []
        conflicted = self._git(["diff", "--name-only", "--diff-filter=U"], path, "").output.splitlines()
        if not conflicted:
            raise GitFailed(f"`git merge --no-edit origin/{base}` failed in {path}: {done.output.strip()[-self.OUTPUT_TAIL :]}")
        return conflicted

    def marked(self, path: Path, files: Sequence[str]) -> List[str]:
        """The files that still hold a conflict marker."""
        return [name for name in files if (path / name).is_file() and self.CONFLICT_MARK.search((path / name).read_text(errors="replace"))]

    def abort_merge(self, path: Path) -> None:
        """Drop a merge that could not be finished, so no conflict marker is ever committed."""
        self._git(["merge", "--abort"], path, "")

    def finish_merge(self, path: Path, identity: GitIdentity) -> None:
        """Commit the merge, even when the result equals the branch (it still records the merge)."""
        self._git(["add", "-A"], path, "")
        self._git([*identity.args(), "commit", "--no-edit"], path, "")

    def commit_all(self, path: Path, message: str, identity: GitIdentity) -> bool:
        """Commit every change of the worktree; False when there was nothing to commit."""
        self._git(["add", "-A"], path, "")
        if self._shell.program(["git", "diff", "--cached", "--quiet"], path).ok():
            return False
        self._git([*identity.args(), "commit", "-m", message], path, "")
        return True

    def diff(self, path: Path, base: str) -> str:
        """The change of the branch since it left `origin/<base>`."""
        return self.changes(path, f"origin/{base}...HEAD")

    def changes(self, path: Path, revisions: str) -> str:
        """`git diff <revisions>`: `origin/main...HEAD` for a ticket, `<sha>..HEAD` for a round."""
        return self._git(["diff", revisions], path, "").output

    def head(self, path: Path) -> str:
        return self._git(["rev-parse", "HEAD"], path, "").output.strip()

    def verify_copy(self, path: Path) -> Path:
        """A clean detached worktree of HEAD next to `path`: the commit without the files git
        ignores, so verify checks what is pushed, not what only the task folder holds."""
        copy = path.with_name(f"{path.name}.verify")
        self.drop_copy(path, copy)
        with self._shared_clone():
            self._git(["worktree", "add", "--detach", str(copy), "HEAD"], path, "")
        return copy

    def drop_copy(self, path: Path, copy: Path) -> None:
        shutil.rmtree(copy, ignore_errors=True)
        with self._shared_clone():
            self._git(["worktree", "prune"], path, "")

    def ignored(self, path: Path) -> List[str]:
        """The files of the worktree that git ignores: they never reach the commit."""
        lines = self._git(["status", "--porcelain", "--ignored"], path, "").output.splitlines()
        return [line[len(self.IGNORED) :] for line in lines if line.startswith(self.IGNORED)]

    def push(self, path: Path, branch: str, token: str) -> None:
        """Push the branch; never with force (rules no_force_push)."""
        self._git(["push", "origin", f"HEAD:refs/heads/{branch}"], path, token)

    def _git(self, args: Sequence[str], cwd: Path, token: str) -> ShellResult:
        if not token:
            return self._checked(["git"], args, cwd)
        with tempfile.TemporaryDirectory(prefix="briar-git-") as scratch:
            secret = Path(scratch) / "token"
            secret.write_text(token)
            secret.chmod(self.SECRET_MODE)
            helper = self.HELPER.format(user=self._remote.user, path=shlex.quote(str(secret)))
            return self._checked(["git", "-c", "credential.helper=", "-c", f"credential.helper={helper}"], args, cwd)

    def _checked(self, prefix: Sequence[str], args: Sequence[str], cwd: Path) -> ShellResult:
        """Run `prefix + args`; a failure names `args` only, never the credential helper."""
        answer = self._shell.program([*prefix, *args], cwd)
        if not answer.ok():
            raise GitFailed(f"`git {shlex.join(args)}` failed in {cwd}: {answer.output.strip()[-self.OUTPUT_TAIL :]}")
        return answer


__all__ = ["GitFailed", "GitIdentity", "Remote", "RepoCheckout", "Shell", "ShellResult"]
