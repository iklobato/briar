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
        prfix.add_argument(
            "--dry-run",
            action="store_true",
            help="Build + print the system prompt + user message + tool list, skip the LLM call. "
            "Validates the JIT context wiring (pr-review-context) without spending tokens.",
        )

        # ─── `implement` subcommand — engineer archetype on one ticket ─────
        implement = sub.add_parser(
            "implement",
            help="Implement one ticket end-to-end (engineer archetype). Clones default branch, fetches ticket-context, runs the agent.",
        )
        implement.add_argument("--company", required=True, help="Company key — must match a runbook YAML")
        implement.add_argument("--owner", required=True, help="Repository owner (GitHub) or workspace (Bitbucket)")
        implement.add_argument("--repo", required=True, help="Repository name / slug")
        implement.add_argument("--ticket-project", required=True, help="Tracker project key (Jira: PROJ; Linear team: ENG; GH/BB Issues: owner/repo)")
        implement.add_argument("--ticket-key", required=True, help="Ticket identifier (Jira: PROJ-123; GH/BB: #42; Linear: ENG-7)")
        implement.add_argument(
            "--tracker",
            default="jira",
            help="Tracker provider for the ticket (default: jira). One of: jira, github-issues, bitbucket-issues, linear.",
        )
        implement.add_argument(
            "--provider",
            default="github",
            help="Repository provider (default: github). One of: github, bitbucket.",
        )
        implement.add_argument("--store", default="postgres", choices=["file", "postgres"], help="KnowledgeStore backend")
        implement.add_argument("--knowledge", default="./knowledge", help="File-store root (ignored for postgres)")
        implement.add_argument("--model", default="", help="Override Anthropic model (defaults to AgentRunner.DEFAULT_MODEL)")
        implement.add_argument("--max-iter", type=int, default=0, help="Iteration ceiling (defaults to AgentRunner.DEFAULT_MAX_ITERATIONS)")
        implement.add_argument("--git-user-name", default="iklobato", help="git config user.name on the worktree")
        implement.add_argument("--git-user-email", default="dev@users.noreply.github.com", help="git config user.email on the worktree")
        implement.add_argument(
            "--keep-worktree",
            action="store_true",
            help="Leave the worktree in /tmp after the run for inspection (default: remove on success)",
        )
        implement.add_argument(
            "--dry-run",
            action="store_true",
            help="Build + print the system prompt + user message + tool list, skip the LLM call. "
            "Validates the JIT context wiring (ticket-context) without spending tokens.",
        )

    def run(self, args: argparse.Namespace) -> int:
        op = args.agent_op
        if op == "prfix":
            return self._run_prfix(args)
        if op == "implement":
            return self._run_implement(args)
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
        log.info(
            "agent-prfix: target=%s pr=%d branch=%s worktree=%s dry_run=%s",
            target,
            args.pr,
            args.branch,
            worktree,
            args.dry_run,
        )

        # Skip the clone in dry-run — the worktree path is only needed
        # as a string in the system prompt (renders fine), the agent
        # never executes against the filesystem.
        if not args.dry_run:
            if not self._clone_branch(clone_url, args.branch, worktree):
                log.error("agent-prfix: clone failed; aborting")
                self._cleanup_worktree(worktree, keep=args.keep_worktree)
                return 4

            if not self._set_git_identity(worktree, args.git_user_name, args.git_user_email):
                log.error("agent-prfix: git identity setup failed; aborting")
                self._cleanup_worktree(worktree, keep=args.keep_worktree)
                return 5

        # JIT-fetch the PR's review comments + failing-CI context. Spliced
        # into the agent's system prompt below the archetype's persona.
        # Failure here is non-fatal — the agent still has the worktree
        # and the bash tool; the pr-review-context is enrichment.
        task_sections = self._fetch_pr_context(
            company=args.company,
            owner=args.owner,
            repo=args.repo,
            pr=args.pr,
        )

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
            task_context_sections=task_sections,
            dry_run=args.dry_run,
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

    def _run_implement(self, args: argparse.Namespace) -> int:
        """Implement one ticket end-to-end via the engineer archetype.

        Parallel to `_run_prfix` but anchored on a ticket key instead
        of a PR number. Clones the default branch (the agent creates
        its own feature branch + pushes + opens a PR); fetches the
        full ticket body via the ticket-context task-scoped extractor;
        splices it into the agent's system prompt."""
        from briar.storage import make_store

        try:
            store = make_store(args.store, file_root=Path(args.knowledge))
        except Exception:  # noqa: BLE001
            log.exception("agent-implement: failed to open store=%s", args.store)
            return 3

        target = f"{args.owner}/{args.repo}"

        worktree = Path(tempfile.mkdtemp(prefix="briar-agent-implement-"))
        log.info(
            "agent-implement: target=%s ticket=%s tracker=%s provider=%s worktree=%s dry_run=%s",
            target,
            args.ticket_key,
            args.tracker,
            args.provider,
            worktree,
            args.dry_run,
        )

        if not args.dry_run:
            if not self._clone_default(args.provider, args.owner, args.repo, worktree, company=args.company):
                log.error("agent-implement: clone failed; aborting")
                self._cleanup_worktree(worktree, keep=args.keep_worktree)
                return 4

            if not self._set_git_identity(worktree, args.git_user_name, args.git_user_email):
                log.error("agent-implement: git identity setup failed; aborting")
                self._cleanup_worktree(worktree, keep=args.keep_worktree)
                return 5

        # JIT-fetch the ticket's full body + ACs + comments. Spliced
        # into the agent's system prompt below the archetype's persona.
        # Failure here is non-fatal — the agent can still proceed with
        # whatever the runbook-blob's `active-tickets` summary had, but
        # we log a warning since the ticket-context is the engineer
        # archetype's #1 priority input.
        task_sections = self._fetch_ticket_context(
            company=args.company,
            tracker=args.tracker,
            ticket_project=args.ticket_project,
            ticket_key=args.ticket_key,
        )
        if not task_sections:
            log.warning("agent-implement: ticket-context was empty — agent will rely on ticket key alone")

        runner = AgentRunner(
            company=args.company,
            task="implement",
            archetype_name="engineer",
            workdir=worktree,
            knowledge_store=store,
            target=target,
            model=args.model,
            max_iterations=args.max_iter,
            extra_user_instructions=self._implement_specific_instructions(
                provider=args.provider,
                company=args.company,
                owner=args.owner,
                repo=args.repo,
                ticket_key=args.ticket_key,
            ),
            task_context_sections=task_sections,
            dry_run=args.dry_run,
        )
        result = runner.run()

        log.info(
            "agent-implement: done iterations=%d stop=%s commits=%d tool_calls=%d %s%s",
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
    def _clone_default(provider: str, owner: str, repo: str, dest: Path, *, company: str = "") -> bool:
        """Clone the default branch (no `--branch` flag), embedding an
        auth token for HTTPS in headless environments. Token-stripping
        cleanup matches `_clone_branch`. provider='github' uses
        ``GITHUB_TOKEN`` + the GitHub `x-access-token` username
        convention; provider='bitbucket' uses
        ``BITBUCKET_<COMPANY>_APP_PASSWORD`` + Bitbucket's
        `x-token-auth` username convention."""
        import os

        if provider == "bitbucket":
            from briar.env_vars import CredEnv

            token = (CredEnv.BITBUCKET_APP_PASSWORD.read(company=company) or "").strip() if company else ""
            if not token:
                log.error("clone failed: BITBUCKET_<COMPANY>_APP_PASSWORD missing for company=%r", company)
                return False
            clone_url = f"https://bitbucket.org/{owner}/{repo}.git"
            authed_url = clone_url.replace("https://bitbucket.org/", f"https://x-token-auth:{token}@bitbucket.org/")
            scrub_host = "https://bitbucket.org/"
        else:
            token = os.environ.get("GITHUB_TOKEN", "").strip()
            if not token:
                log.error("clone failed: GITHUB_TOKEN env var missing")
                return False
            clone_url = f"https://github.com/{owner}/{repo}.git"
            authed_url = clone_url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/")
            scrub_host = "https://github.com/"

        log.debug("clone-default: provider=%s dest=%s (token redacted)", provider, dest)
        proc = subprocess.run(
            ["git", "clone", "--depth", "50", authed_url, str(dest)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.replace(token, "<TOKEN>").strip()[:400]
            log.error("clone failed: rc=%d stderr=%s", proc.returncode, stderr)
            return False
        reset = subprocess.run(
            ["git", "-C", str(dest), "remote", "set-url", "origin", clone_url],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if reset.returncode != 0:
            log.warning("clone-default: remote set-url cleanup failed (token may persist in .git/config) host=%s", scrub_host)
        return True

    @staticmethod
    def _fetch_ticket_context(*, company: str, tracker: str, ticket_project: str, ticket_key: str):
        """Run the `ticket-context` task-scoped extractor. Returns a
        list with one ExtractedSection or empty on failure. Symmetric
        to `_fetch_pr_context` but for the engineer flow."""
        import argparse as _ap

        from briar.extract import TASK_SCOPED_EXTRACTORS

        extractor = TASK_SCOPED_EXTRACTORS.get("ticket-context")
        if extractor is None:
            return []
        ns = _ap.Namespace(
            company=company,
            tracker=tracker,
            ticket_project=ticket_project,
            ticket_key=ticket_key,
        )
        try:
            section = extractor.fetch(ns)
        except Exception:  # noqa: BLE001
            log.exception("ticket-context fetch failed; agent continues without it")
            return []
        if getattr(section, "is_empty", True):
            return []
        log.info("ticket-context: title=%r body_bytes=%d", section.title, len(section.body or ""))
        return [section]

    @staticmethod
    def _implement_specific_instructions(*, provider: str, company: str, owner: str, repo: str, ticket_key: str) -> str:
        branch = f"briar/{ticket_key.lower().replace('#','').replace(' ', '-')}"
        common = (
            f"The target ticket is {ticket_key} on {owner}/{repo}. The worktree is a fresh clone of the "
            "default branch. Procedure:\n"
            "  1. Read the ticket-context section above for the full body + acceptance criteria. Address EVERY AC.\n"
            f"  2. Create a feature branch: `git checkout -b {branch}`.\n"
            "  3. Make the change. Match codebase-conventions (test runner, linter, formatter, migration tool).\n"
            "  4. Run the test command from codebase-conventions locally; only push when it's green.\n"
            "  5. Push: `git push -u origin HEAD` (NEVER --force).\n"
        )
        if provider == "bitbucket":
            # Bitbucket Cloud has no first-party CLI; use the v2 REST API via curl.
            # Workspace access token lives in BITBUCKET_<COMPANY>_APP_PASSWORD;
            # auth header is HTTP basic with username `x-token-auth`.
            env_token = f"BITBUCKET_{company.upper().replace('-', '_')}_APP_PASSWORD"
            return common + (
                f"  6. Open a draft PR via the Bitbucket v2 API. The workspace access token is in env var "
                f"`{env_token}`. Auth: `-u 'x-token-auth:$"+env_token+"'`. Endpoint: "
                f"`POST https://api.bitbucket.org/2.0/repositories/{owner}/{repo}/pullrequests`. "
                f"Body JSON fields: `title`, `description`, `source.branch.name` (= `{branch}`), `draft: true`. "
                "The response's `links.html.href` is the PR URL.\n"
                "  7. End your output with the PR URL on its own line. No fictitious URLs — if the curl fails, surface the error verbatim.\n"
                "\n"
                "Strict constraints: NEVER --force, --amend, rebase, squash. NEVER commit as a bot identity "
                "(run `git config user.name` to verify it's a human). If an AC is ambiguous, stop and post a "
                "clarifying comment on the ticket — do not guess."
            )
        return common + (
            "  6. Open a draft PR via `gh pr create --draft --title '<key>: <short>' --body '<plan + test plan + risks>'`.\n"
            "  7. End your output with the PR URL on its own line. No fictitious URLs — if `gh pr create` fails, surface the error.\n"
            "\n"
            "Strict constraints: NEVER --force, --amend, rebase, squash. NEVER commit as a bot identity "
            "(run `git config user.name` to verify it's a human). If an AC is ambiguous, stop and post a "
            "clarifying comment on the ticket — do not guess."
        )

    @staticmethod
    def _clone_branch(clone_url: str, branch: str, dest: Path) -> bool:
        """Clone via HTTPS with the GITHUB_TOKEN embedded in the URL.

        The droplet has no git credential helper configured for
        github.com (and `gh` isn't installed), so plain `git clone
        https://github.com/...` fails with `could not read Username for
        'https://github.com'`. Standard CI workaround: inject the token
        as the username field. The token comes from $GITHUB_TOKEN — same
        env var the rest of briar uses (sourced from /etc/briar/secrets.env
        on the droplet via `set -a; . /etc/briar/secrets.env`).

        The token is stripped from the resulting remote URL after clone
        so it does not linger in .git/config on disk."""
        import os

        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            log.error("clone failed: GITHUB_TOKEN env var missing")
            return False
        authed_url = clone_url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/")
        log.debug("clone-branch: branch=%s dest=%s (token redacted)", branch, dest)
        proc = subprocess.run(
            ["git", "clone", "--depth", "50", "--branch", branch, authed_url, str(dest)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            # Redact the token from the URL in case it leaked into stderr.
            stderr = proc.stderr.replace(token, "<TOKEN>").strip()[:400]
            log.error("clone failed: rc=%d stderr=%s", proc.returncode, stderr)
            return False
        # Strip the embedded token from the persisted remote so anyone
        # who looks at .git/config later doesn't see it.
        reset = subprocess.run(
            ["git", "-C", str(dest), "remote", "set-url", "origin", clone_url],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if reset.returncode != 0:
            log.warning("clone: remote set-url cleanup failed (token may persist in .git/config)")
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
    def _fetch_pr_context(*, company: str, owner: str, repo: str, pr: int):
        """Run the `pr-review-context` task-scoped extractor for this
        PR. Returns a list with one ExtractedSection or empty on failure.
        Defensive — the agent should still run if this fetch fails."""
        import argparse as _ap

        from briar.extract import TASK_SCOPED_EXTRACTORS

        extractor = TASK_SCOPED_EXTRACTORS.get("pr-review-context")
        if extractor is None:
            return []
        ns = _ap.Namespace(
            company=company,
            provider="github",  # CommandAgent.prfix is GitHub-only today; Bitbucket variant would override.
            pr_target_repo=f"{owner}/{repo}",
            pr_target_number=pr,
        )
        try:
            section = extractor.fetch(ns)
        except Exception:  # noqa: BLE001
            log.exception("pr-review-context fetch failed; agent continues without it")
            return []
        if getattr(section, "is_empty", True):
            return []
        log.info("pr-review-context: title=%r body_bytes=%d", section.title, len(section.body or ""))
        return [section]

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
