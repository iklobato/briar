"""`briar agent prfix` — autonomous PR-fixer runner.

Loads the company's knowledge from the configured store, opens a clean
git worktree on the target PR's branch, then drives the pr-fixer
archetype through the Anthropic API loop until completion or guardrail.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from briar.agent.runner import AgentRunner
from briar.commands.base import Command


log = logging.getLogger(__name__)


class CommandAgent(Command):
    name = "agent"
    help = "Run an autonomous agent flow against a target (prfix / conflict-resolve / ci-fix)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="agent_op", required=True, metavar="OP")
        prfix = sub.add_parser("prfix", help="Address open review comments on a PR (pr-fixer archetype).")
        prfix.add_argument("--company", required=True, help="Company key — must match a runbook YAML")
        prfix.add_argument("--owner", required=True, help="GitHub owner of the target repo")
        prfix.add_argument("--repo", required=True, help="GitHub repo name")
        prfix.add_argument("--pr", type=int, required=True, help="PR number to address")
        prfix.add_argument("--branch", required=True, help="PR head branch name")
        prfix.add_argument("--store", default="postgres", choices=["file", "postgres"], help="KnowledgeStore backend")
        prfix.add_argument("--knowledge", default="./knowledge", help="File-store root (ignored for postgres)")
        prfix.add_argument(
            "--model",
            default="",
            help="Override Anthropic model (defaults to AgentRunner.DEFAULT_MODEL)",
        )
        prfix.add_argument(
            "--max-iter",
            type=int,
            default=0,
            help="Iteration ceiling (defaults to AgentRunner.DEFAULT_MAX_ITERATIONS)",
        )
        prfix.add_argument(
            "--git-user-name",
            default="iklobato",
            help="git config user.name to set on the worktree before any commit",
        )
        prfix.add_argument(
            "--git-user-email",
            default="dev@users.noreply.github.com",
            help="git config user.email to set on the worktree before any commit",
        )
        prfix.add_argument(
            "--keep-worktree",
            action="store_true",
            help="Leave the worktree in /tmp after the run for inspection (default: remove on success)",
        )

    def run(self, args: argparse.Namespace) -> int:
        op = args.agent_op
        if op == "prfix":
            return self._run_prfix(args)
        log.error("unknown agent op: %s", op)
        return 2

    def _run_prfix(self, args: argparse.Namespace) -> int:
        from briar.storage import make_store

        try:
            store = make_store(args.store, file_root=Path(args.knowledge))
        except Exception:  # noqa: BLE001
            log.exception("agent-prfix: failed to open store=%s", args.store)
            return 3

        target = f"{args.owner}/{args.repo}"
        clone_url = f"https://github.com/{target}.git"

        worktree = Path(tempfile.mkdtemp(prefix="briar-agent-prfix-"))
        log.info("agent-prfix: target=%s pr=%d branch=%s worktree=%s", target, args.pr, args.branch, worktree)

        if not self._clone_branch(clone_url, args.branch, worktree):
            log.error("agent-prfix: clone failed; aborting")
            self._cleanup_worktree(worktree, keep=args.keep_worktree)
            return 4

        if not self._set_git_identity(worktree, args.git_user_name, args.git_user_email):
            log.error("agent-prfix: git identity setup failed; aborting")
            self._cleanup_worktree(worktree, keep=args.keep_worktree)
            return 5

        runner = AgentRunner(
            company=args.company,
            task="prfix",
            archetype_name="pr-fixer",
            workdir=worktree,
            knowledge_store=store,
            target=target,
            model=args.model,
            max_iterations=args.max_iter,
            extra_user_instructions=self._pr_specific_instructions(args.owner, args.repo, args.pr, args.branch),
        )
        result = runner.run()

        log.info(
            "agent-prfix: done iterations=%d stop=%s commits=%d tool_calls=%d %s%s",
            result.iterations,
            result.stop_reason,
            len(result.commits),
            result.tool_calls,
            result.cost_summary(),
            f" error={result.error!r}" if result.error else "",
        )
        if result.final_text:
            print("--- agent final text ---")
            print(result.final_text)
        if result.commits:
            print(f"--- commits: {', '.join(result.commits)} ---")
        self._cleanup_worktree(worktree, keep=args.keep_worktree or bool(result.error))
        return 0 if not result.error else 6

    @staticmethod
    def _clone_branch(clone_url: str, branch: str, dest: Path) -> bool:
        """Clone via `gh repo clone` so the droplet's GH auth chain is
        used. `git clone https://...` would fail because the droplet has
        no credential helper for github.com — but `gh` IS authenticated
        (GITHUB_TOKEN sourced from /etc/briar/secrets.env at runtime)."""
        owner_repo = clone_url.replace("https://github.com/", "").replace(".git", "")
        log.debug("clone-branch: gh repo clone %s --branch %s -> %s", owner_repo, branch, dest)
        proc = subprocess.run(
            [
                "gh",
                "repo",
                "clone",
                owner_repo,
                str(dest),
                "--",
                "--depth",
                "50",
                "--branch",
                branch,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            log.error("clone failed: rc=%d stderr=%s", proc.returncode, proc.stderr.strip()[:400])
            return False
        return True

    @staticmethod
    def _set_git_identity(worktree: Path, user_name: str, user_email: str) -> bool:
        for key, value in (("user.name", user_name), ("user.email", user_email), ("commit.gpgsign", "false")):
            proc = subprocess.run(
                ["git", "-C", str(worktree), "config", key, value],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                log.error("git config %s failed: %s", key, proc.stderr.strip())
                return False
        return True

    @staticmethod
    def _pr_specific_instructions(owner: str, repo: str, pr: int, branch: str) -> str:
        return (
            f"The target is PR #{pr} on {owner}/{repo}, branch {branch}. The worktree is a fresh clone at "
            "the branch HEAD. Use `gh pr view {pr} --repo {owner}/{repo}` for state, "
            "`gh api repos/{owner}/{repo}/pulls/{pr}/comments` for inline threads, "
            "`gh api repos/{owner}/{repo}/issues/{pr}/comments` for PR-level comments. "
            "Push with `git push origin HEAD:{branch}` (NEVER --force). Reply to threads "
            "via `gh api -X POST repos/{owner}/{repo}/pulls/{pr}/comments/<id>/replies -f body=...`."
        ).format(owner=owner, repo=repo, pr=pr, branch=branch)

    @staticmethod
    def _cleanup_worktree(path: Path, *, keep: bool) -> None:
        if keep:
            log.info("worktree kept at %s for inspection", path)
            return
        try:
            shutil.rmtree(path)
        except OSError:
            log.exception("worktree cleanup failed: %s", path)
