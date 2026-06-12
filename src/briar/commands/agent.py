"""`briar agent` — autonomous agent runner.

Two ops today (`prfix`, `implement`); future ops register by adding a
subclass to `AGENT_OPS`. The dispatcher (`CommandAgent.run`) does a
registry lookup, NOT an if-chain — same Strategy + Registry shape as
every other plugin family in the codebase.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

from briar._registry import build_registry
from briar.agent.runner import AgentRunConfig, AgentRunner
from briar.commands._enums import ExitCode
from briar.commands.base import Subcommand, SubcommandCommand
from briar.errors import CliError

log = logging.getLogger(__name__)


# ─── AgentOp Strategy + Registry ────────────────────────────────────────────
#
# Each op owns its own `add_arguments` + `run`; dispatch + subparser wiring
# are inherited from SubcommandCommand. Adding a new op = one subclass + one
# entry in AGENT_OPS.


class AgentOp(Subcommand):
    """One `briar agent` subcommand (`prfix`, `implement`, …)."""


def _add_common_agent_arguments(parser: argparse.ArgumentParser) -> None:
    """Flags shared verbatim by every agent op (target, store, model,
    git identity, worktree). Op-specific flags (`--pr`/`--branch` vs
    `--ticket-*`) and flags whose help text differs per op (`--dry-run`,
    `--runbook`) stay in the op's own `add_arguments`."""
    parser.add_argument("--company", required=True, help="Company key — must match a runbook YAML")
    parser.add_argument("--owner", required=True, help="Repository owner (GitHub) or workspace (Bitbucket)")
    parser.add_argument("--repo", required=True, help="Repository name / slug")
    parser.add_argument("--provider", default="github", help="Repository provider (default: github). One of: github, bitbucket.")
    parser.add_argument("--store", default="postgres", choices=["file", "postgres"], help="KnowledgeStore backend")
    parser.add_argument("--knowledge", default="./knowledge", help="File-store root (ignored for postgres)")
    parser.add_argument("--model", default="", help="Override Anthropic model (defaults to AgentRunner.DEFAULT_MODEL)")
    parser.add_argument("--max-iter", type=int, default=0, help="Iteration ceiling (defaults to AgentRunner.DEFAULT_MAX_ITERATIONS)")
    parser.add_argument(
        "--git-user-name", default="", help="git config user.name on the worktree. Required unless company.git_identity.name is set in the runbook."
    )
    parser.add_argument(
        "--git-user-email", default="", help="git config user.email on the worktree. Required unless company.git_identity.email is set in the runbook."
    )
    parser.add_argument("--keep-worktree", action="store_true", help="Leave the worktree in /tmp after the run for inspection")


def _add_meeting_arguments(parser: argparse.ArgumentParser, *, query_help: str) -> None:
    """Meeting-context wiring (Fireflies + future vendors). All optional —
    absent flags = no meeting fetch. Only `--meeting-query`'s help text
    differs per op, so it's parameterised."""
    parser.add_argument("--meeting", default="fireflies", help="Meeting provider for transcript fetch (default: fireflies)")
    parser.add_argument("--meeting-key", default="", help="Specific meeting ID to splice into the agent prompt")
    parser.add_argument("--meeting-query", default="", help=query_help)
    parser.add_argument("--meeting-top-k", type=int, default=3, help="Max meetings to fetch in search mode (default: 3)")
    parser.add_argument("--meeting-max-bytes", type=int, default=50_000, help="Per-meeting transcript byte cap (default: 50000)")


class PrfixOp(AgentOp):
    name = "prfix"
    help = "Address open review comments on a PR (pr-fixer archetype)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        _add_common_agent_arguments(parser)
        parser.add_argument("--pr", type=int, required=True, help="PR number to address")
        parser.add_argument("--branch", required=True, help="PR head branch name")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Build + print the system prompt + user message + tool list, skip the LLM call. "
            "Validates the JIT context wiring (pr-review-context) without spending tokens.",
        )
        parser.add_argument(
            "--runbook",
            default="",
            help="Optional runbook YAML to read this company's `messages:` block from. "
            "When set, the agent gets a `send_message` tool bound to the configured channels "
            "instead of having to shell out via `gh` / `curl`.",
        )
        _add_meeting_arguments(
            parser,
            query_help="Keyword search across recent meetings. When omitted, defaults to the PR's owner/repo#pr — "
            "set explicitly to override (e.g. the reviewer's name or a topic).",
        )

    def run(self, agent_cmd: "CommandAgent", args: argparse.Namespace) -> int:
        return agent_cmd._run_prfix(args)


class ImplementOp(AgentOp):
    name = "implement"
    help = "Implement one ticket end-to-end (engineer archetype). Clones default branch, fetches ticket-context, runs the agent."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        _add_common_agent_arguments(parser)
        parser.add_argument("--ticket-project", required=True, help="Tracker project key (Jira: PROJ; Linear team: ENG; GH/BB Issues: owner/repo)")
        parser.add_argument("--ticket-key", required=True, help="Ticket identifier (Jira: PROJ-123; GH/BB: #42; Linear: ENG-7)")
        parser.add_argument(
            "--tracker", default="jira", help="Tracker provider for the ticket (default: jira). One of: jira, github-issues, bitbucket-issues, linear."
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Build + print the system prompt + user message + tool list, skip the LLM call. "
            "Validates the JIT context wiring (ticket-context) without spending tokens.",
        )
        parser.add_argument(
            "--runbook",
            default="",
            help="Optional runbook YAML to read this company's `messages:` block from.",
        )
        _add_meeting_arguments(
            parser,
            query_help="Keyword search across recent meetings. When omitted, defaults to the ticket key — "
            "set explicitly to override (e.g. a topic or feature name).",
        )

    def run(self, agent_cmd: "CommandAgent", args: argparse.Namespace) -> int:
        return agent_cmd._run_implement(args)


AGENT_OPS: Dict[str, AgentOp] = build_registry(
    (PrfixOp(), ImplementOp()),
    kind="agent op",
)


def run_implement(args: argparse.Namespace) -> int:
    """Public seam — drive one `agent implement` run from outside this
    module without depending on CommandAgent's private API.

    `briar plan run` calls this in its iteration loop; CommandAgent's
    own dispatch path calls `_run_implement` directly. Both produce the
    same return code (0 = success; 1 = error; 2 = usage). Granular
    pre-LLM exit codes (3-6) were collapsed in Phase 8.

    The arg-shape stays argparse.Namespace because that's the existing
    contract — a future refactor to a typed `ImplementRequest` dataclass
    would land separately. Today's caller (CommandPlan.RunOp) builds the
    Namespace by hand from the loop's plan card."""
    return CommandAgent()._run_implement(args)


class CommandAgent(SubcommandCommand):
    name = "agent"
    help = "Run an autonomous agent flow against a target (prfix / implement)."
    dest = "agent_op"
    op_noun = "agent op"
    ops = AGENT_OPS

    def _run_prfix(self, args: argparse.Namespace) -> int:
        setup = self._prepare_agent_workdir(args, op_name="prfix", clone_branch=args.branch)
        if isinstance(setup, int):
            return setup  # ExitCode propagated from the setup failure
        worktree, provider, store = setup
        target = f"{args.owner}/{args.repo}"
        log.info(
            "agent-prfix: provider=%s target=%s pr=%d branch=%s worktree=%s dry_run=%s",
            provider.kind,
            target,
            args.pr,
            args.branch,
            worktree,
            args.dry_run,
        )

        # JIT-fetch the PR's review comments + failing-CI context.
        # Failure here is non-fatal — the agent still has the worktree
        # and the bash tool; pr-review-context is enrichment.
        task_sections = self._fetch_pr_context(
            company=args.company,
            provider=args.provider,
            owner=args.owner,
            repo=args.repo,
            pr=args.pr,
        )
        # Default meeting query = PR identifier so the prfix flow finds
        # any meeting that mentioned the PR or its feature.
        default_query = f"{args.owner}/{args.repo}#{args.pr}"
        task_sections += self._fetch_meeting_context_from_args(args, default_query)

        result = AgentRunner(
            AgentRunConfig(
                company=args.company,
                task="prfix",
                archetype_name="pr-fixer",
                workdir=worktree,
                knowledge_store=store,
                target=target,
                model=args.model,
                max_iterations=args.max_iter,
                extra_user_instructions=self._pr_specific_instructions(
                    args.owner,
                    args.repo,
                    args.pr,
                    args.branch,
                ),
                task_context_sections=tuple(task_sections),
                dry_run=args.dry_run,
                messages=self._load_messages_block(args),
                mcp_servers=self._load_mcp_block(args),
            )
        ).run()

        return self._finalize_agent_result(args, op_name="prfix", worktree=worktree, result=result)

    def _run_implement(self, args: argparse.Namespace) -> int:
        """Implement one ticket end-to-end via the engineer archetype.

        Parallel to `_run_prfix` but anchored on a ticket key instead
        of a PR number. Clones the default branch (the agent creates
        its own feature branch + pushes + opens a PR); fetches the
        full ticket body via the ticket-context task-scoped extractor;
        splices it into the agent's system prompt."""
        setup = self._prepare_agent_workdir(args, op_name="implement")
        if isinstance(setup, int):
            return setup
        worktree, provider, store = setup
        target = f"{args.owner}/{args.repo}"
        log.info(
            "agent-implement: target=%s ticket=%s tracker=%s provider=%s worktree=%s dry_run=%s",
            target,
            args.ticket_key,
            args.tracker,
            args.provider,
            worktree,
            args.dry_run,
        )

        # JIT-fetch the ticket's full body + ACs + comments. Failure is
        # non-fatal — the agent can still proceed with the active-tickets
        # summary — but log a warning because the ticket-context is the
        # engineer archetype's #1 priority input.
        task_sections = self._fetch_ticket_context(
            company=args.company,
            tracker=args.tracker,
            ticket_project=args.ticket_project,
            ticket_key=args.ticket_key,
        )
        if not task_sections:
            log.warning("agent-implement: ticket-context was empty — agent will rely on ticket key alone")

        # Default meeting query = ticket key; Fireflies' keyword search
        # surfaces any meeting that mentioned ACME-123 in title or body.
        task_sections += self._fetch_meeting_context_from_args(args, args.ticket_key)

        result = AgentRunner(
            AgentRunConfig(
                company=args.company,
                task="implement",
                archetype_name="engineer",
                workdir=worktree,
                knowledge_store=store,
                target=target,
                model=args.model,
                max_iterations=args.max_iter,
                extra_user_instructions=self._implement_specific_instructions(
                    provider=provider,
                    owner=args.owner,
                    repo=args.repo,
                    ticket_key=args.ticket_key,
                ),
                task_context_sections=tuple(task_sections),
                dry_run=args.dry_run,
                messages=self._load_messages_block(args),
                mcp_servers=self._load_mcp_block(args),
            )
        ).run()

        return self._finalize_agent_result(args, op_name="implement", worktree=worktree, result=result)

    def _prepare_agent_workdir(
        self,
        args: argparse.Namespace,
        *,
        op_name: str,
        clone_branch: str = "",
    ) -> "Tuple[Path, Any, Any] | int":
        """Open the knowledge store, build the repo provider, mkdtemp
        a worktree, clone + set git identity unless `--dry-run`.

        Returns ``(worktree, provider, store)`` on success, or an
        ``ExitCode`` int when setup fails so the caller can short-
        circuit with the right exit code. Worktree cleanup on partial-
        setup failure is handled here.

        Extracted from `_run_prfix` / `_run_implement` (Phase 13) —
        both methods used to inline ~30 lines of identical setup code."""
        from briar.extract._providers import make_provider
        from briar.storage import make_store

        log_prefix = f"agent-{op_name}"
        try:
            store = make_store(args.store, file_root=Path(args.knowledge))
        except Exception:  # noqa: BLE001
            log.exception("%s: failed to open store=%s", log_prefix, args.store)
            return ExitCode.GENERAL_ERROR

        try:
            provider = make_provider(args.provider, company=args.company)
        except Exception:  # noqa: BLE001 — `make_provider` raises CliError on unknown kind
            log.exception("%s: failed to construct provider=%s", log_prefix, args.provider)
            return ExitCode.USAGE_ERROR

        worktree = Path(tempfile.mkdtemp(prefix=f"briar-agent-{op_name}-"))

        # Skip the clone in dry-run — the worktree path is only needed
        # as a string in the system prompt (renders fine), the agent
        # never executes against the filesystem.
        if not args.dry_run:
            if not self._clone(provider, args.owner, args.repo, worktree, branch=clone_branch):
                log.error("%s: clone failed; aborting", log_prefix)
                self._cleanup_worktree(worktree, keep=args.keep_worktree)
                return ExitCode.GENERAL_ERROR
            git_name, git_email = self._resolve_git_identity(args)
            log.info("%s: git identity user.name=%s user.email=%s", log_prefix, git_name, git_email)
            if not self._set_git_identity(worktree, git_name, git_email):
                log.error("%s: git identity setup failed; aborting", log_prefix)
                self._cleanup_worktree(worktree, keep=args.keep_worktree)
                return ExitCode.GENERAL_ERROR

        return worktree, provider, store

    def _fetch_meeting_context_from_args(
        self,
        args: argparse.Namespace,
        default_query: str,
    ) -> list:
        """Read the meeting-* knobs off `args` with sensible defaults
        + dispatch to `_fetch_meeting_context`. Centralises the
        getattr-with-default chain that both prfix and implement
        called inline previously."""
        meeting_query = (getattr(args, "meeting_query", "") or "").strip() or default_query
        return self._fetch_meeting_context(
            company=args.company,
            meeting_kind=getattr(args, "meeting", "fireflies"),
            meeting_key=getattr(args, "meeting_key", ""),
            meeting_query=meeting_query,
            meeting_top_k=getattr(args, "meeting_top_k", 3),
            meeting_max_bytes=getattr(args, "meeting_max_bytes", 50_000),
        )

    def _finalize_agent_result(
        self,
        args: argparse.Namespace,
        *,
        op_name: str,
        worktree: Path,
        result: Any,
    ) -> int:
        """Log result + print final-text / commits + cleanup worktree.
        Shared tail used by every `_run_<op>` method."""
        log.info(
            "agent-%s: done iterations=%d stop=%s commits=%d tool_calls=%d %s%s",
            op_name,
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
        # Keep the worktree on failure even when --keep-worktree was off,
        # so an operator can `cd` into it and inspect what the agent did.
        self._cleanup_worktree(worktree, keep=args.keep_worktree or bool(result.error))
        return ExitCode.OK if not result.error else ExitCode.GENERAL_ERROR

    @staticmethod
    def _clone(provider, owner: str, repo: str, dest: Path, *, branch: str = "") -> bool:
        """Clone via HTTPS with the provider's token injected.

        When ``branch`` is empty, clones the default branch (used by the
        `implement` flow — the agent creates its own feature branch
        afterwards). When ``branch`` is set, clones that specific branch
        (used by the `prfix` flow — the worktree must start at the PR's
        HEAD).

        The provider owns the per-vendor token resolution + URL
        conventions (``resolve_token`` / ``clone_url`` /
        ``authed_clone_url``); this method only knows how to drive
        ``git.Repo.clone_from`` + reset the persisted remote so the
        token does not linger in ``.git/config``."""
        from git import GitCommandError, Repo

        token = provider.resolve_token()
        if not token:
            log.error(
                "clone failed: no token for provider=%s company=%r",
                provider.kind,
                provider.company,
            )
            return False
        clone_url = provider.clone_url(owner, repo)
        authed_url = provider.authed_clone_url(owner, repo, token)
        log.debug("clone: provider=%s branch=%s dest=%s (token redacted)", provider.kind, branch or "(default)", dest)
        kwargs: Dict[str, object] = {"depth": 50}
        if branch:
            kwargs["branch"] = branch
        try:
            repo_obj = Repo.clone_from(authed_url, str(dest), **kwargs)
        except GitCommandError as exc:
            stderr = (exc.stderr or "").replace(token, "<TOKEN>").strip()[:400]
            log.error("clone failed: rc=%s stderr=%s", exc.status, stderr)
            return False
        try:
            repo_obj.remote("origin").set_url(clone_url)
        except (GitCommandError, ValueError) as exc:
            log.warning(
                "clone: remote set-url cleanup failed (token may persist in .git/config) provider=%s err=%s",
                provider.kind,
                exc,
            )
        return True

    @staticmethod
    def _load_messages_block(args: argparse.Namespace):
        """Read the company's `messages:` block from the optional
        --runbook YAML. Returns an empty dict on any failure (the agent
        runs fine without bound message channels — it falls back to
        the bash escape hatch for `gh` / `curl`)."""
        runbook_path = getattr(args, "runbook", "") or ""
        if not runbook_path:
            return {}
        try:
            from briar.iac.runbook import load_runbook_file

            rb = load_runbook_file(Path(runbook_path))
        except Exception:  # noqa: BLE001
            log.exception("failed to load runbook=%s for messages: block — continuing without send_message tool", runbook_path)
            return {}
        company = rb.companies.get(args.company)
        if company is None:
            log.warning("runbook=%s has no company=%s — agent will run without bound messages", runbook_path, args.company)
            return {}
        return dict(getattr(company, "messages", {}) or {})

    @staticmethod
    def _load_mcp_block(args: argparse.Namespace):
        """Read the company's `mcp:` block from the optional --runbook
        YAML. Returns an empty dict on any failure (the agent runs fine
        without MCP servers — they're purely additive tools)."""
        runbook_path = getattr(args, "runbook", "") or ""
        if not runbook_path:
            return {}
        try:
            from briar.iac.runbook import load_runbook_file

            rb = load_runbook_file(Path(runbook_path))
        except Exception:  # noqa: BLE001
            log.exception("failed to load runbook=%s for mcp: block — continuing without MCP tools", runbook_path)
            return {}
        company = rb.companies.get(args.company)
        if company is None:
            log.warning("runbook=%s has no company=%s — agent will run without MCP servers", runbook_path, args.company)
            return {}
        return dict(getattr(company, "mcp", {}) or {})

    @staticmethod
    def _resolve_git_identity(args: argparse.Namespace) -> Tuple[str, str]:
        """Resolve (user_name, user_email) for the worktree's commit identity.

        Priority (per-field, independent):
          1. CLI flag — ``--git-user-name`` / ``--git-user-email`` (non-empty)
          2. Runbook YAML — ``companies.<name>.git_identity.{name, email}``

        Per-field resolution means you can set the name via CLI and let
        the email fall through to YAML (or vice versa). YAML lookup
        runs only when ``--runbook`` was passed; failures during YAML
        load are non-fatal — the resolver logs and the field stays empty.

        Raises ``CliError`` when neither source provides a value for one
        of the fields. There is no hardcoded fallback — committed
        personal identifiers on a third-party host are a smell."""
        cli_name = (getattr(args, "git_user_name", "") or "").strip()
        cli_email = (getattr(args, "git_user_email", "") or "").strip()

        yaml_name = ""
        yaml_email = ""
        runbook_path = getattr(args, "runbook", "") or ""
        if runbook_path:
            try:
                from briar.iac.runbook import load_runbook_file

                rb = load_runbook_file(Path(runbook_path))
                company = rb.companies.get(getattr(args, "company", ""))
                if company is not None:
                    gi = getattr(company, "git_identity", None)
                    if gi is not None:
                        yaml_name = (gi.name or "").strip()
                        yaml_email = (gi.email or "").strip()
            except Exception:  # noqa: BLE001
                log.exception("failed to load runbook=%s for git_identity — staying unset", runbook_path)

        resolved_name = cli_name or yaml_name
        resolved_email = cli_email or yaml_email
        if not resolved_name or not resolved_email:
            raise CliError(
                "git identity not configured: pass --git-user-name/--git-user-email " "or set git_identity.name/.email in the runbook's company block."
            )
        return resolved_name, resolved_email

    @staticmethod
    def _fetch_ticket_context(*, company: str, tracker: str, ticket_project: str, ticket_key: str):
        """Run the `ticket-context` task-scoped extractor. Returns a
        list with one ExtractedSection or empty on failure. Symmetric
        to `_fetch_pr_context` but for the engineer flow."""

        from briar.extract import TASK_SCOPED_EXTRACTORS

        extractor = TASK_SCOPED_EXTRACTORS.get("ticket-context")
        if extractor is None:
            return []
        ns = argparse.Namespace(
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
    def _implement_specific_instructions(*, provider, owner: str, repo: str, ticket_key: str) -> str:
        """Compose the agent's procedure instructions. Lines 1-5 are
        provider-agnostic; lines 6-7 (the PR-creation recipe) come
        from `provider.pr_creation_recipe(...)` so adding a new vendor
        doesn't touch this method.

        `provider` is a constructed `RepositoryProvider`. The caller
        already built one to drive the clone — we reuse it instead of
        looking it up by string here."""
        from briar.plan._models import suggest_branch

        branch = suggest_branch(ticket_key)
        common = (
            f"The target ticket is {ticket_key} on {owner}/{repo}. The worktree is a fresh clone of the "
            "default branch. Procedure:\n"
            "  1. Read the ticket-context section above for the full body + acceptance criteria. Address EVERY AC.\n"
            f"  2. Create a feature branch: `git checkout -b {branch}`.\n"
            "  3. Make the change. Match codebase-conventions (test runner, linter, formatter, migration tool).\n"
            "  4. Run the test command from codebase-conventions locally; only push when it's green.\n"
            "  5. Push: `git push -u origin HEAD` (NEVER --force).\n"
        )
        recipe = provider.pr_creation_recipe(owner=owner, repo=repo, branch=branch)
        return (
            common
            + recipe
            + (
                "\n"
                "Strict constraints: NEVER --force, --amend, rebase, squash. NEVER commit as a bot identity "
                "(run `git config user.name` to verify it's a human). If an AC is ambiguous, stop and post a "
                "clarifying comment on the ticket — do not guess."
            )
        )

    @staticmethod
    def _set_git_identity(worktree: Path, user_name: str, user_email: str) -> bool:
        from git import GitCommandError, InvalidGitRepositoryError, Repo

        try:
            repo = Repo(str(worktree))
        except (InvalidGitRepositoryError, OSError) as exc:
            log.error("git identity: not a git repo at %s (%s)", worktree, exc)
            return False
        entries = (
            ("user", "name", user_name),
            ("user", "email", user_email),
            ("commit", "gpgsign", "false"),
        )
        try:
            with repo.config_writer() as cw:
                for section, option, value in entries:
                    cw.set_value(section, option, value)
        except (GitCommandError, OSError) as exc:
            log.error("git identity write failed: %s", exc)
            return False
        return True

    @staticmethod
    def _fetch_pr_context(*, company: str, provider: str, owner: str, repo: str, pr: int):
        """Run the `pr-review-context` task-scoped extractor for this
        PR. Returns a list with one ExtractedSection or empty on failure.
        Defensive — the agent should still run if this fetch fails."""

        from briar.extract import TASK_SCOPED_EXTRACTORS

        extractor = TASK_SCOPED_EXTRACTORS.get("pr-review-context")
        if extractor is None:
            return []
        ns = argparse.Namespace(
            company=company,
            provider=provider,
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
    def _fetch_meeting_context(
        *,
        company: str,
        meeting_kind: str,
        meeting_key: str,
        meeting_query: str,
        meeting_top_k: int,
        meeting_max_bytes: int,
    ):
        """Run the `meeting-context` task-scoped extractor. Returns a
        list with one ExtractedSection or empty on failure / empty
        result. Symmetric to `_fetch_ticket_context` / `_fetch_pr_context`.

        Failure here is non-fatal — meetings are enrichment, not the
        primary input. The agent runs fine without them."""

        from briar.extract import TASK_SCOPED_EXTRACTORS

        extractor = TASK_SCOPED_EXTRACTORS.get("meeting-context")
        if extractor is None:
            return []
        if not meeting_key and not meeting_query:
            return []
        ns = argparse.Namespace(
            company=company,
            meeting=meeting_kind or "fireflies",
            meeting_key=meeting_key,
            meeting_query=meeting_query,
            meeting_top_k=meeting_top_k,
            meeting_max_bytes=meeting_max_bytes,
        )
        try:
            section = extractor.fetch(ns)
        except Exception:  # noqa: BLE001
            log.exception("meeting-context fetch failed; agent continues without it")
            return []
        if getattr(section, "is_empty", True):
            return []
        log.info("meeting-context: title=%r body_bytes=%d", section.title, len(section.body or ""))
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
