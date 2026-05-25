"""`briar plan` — build / inspect / advance sequenced implementation
plans built from tracker boards.

Subcommands (same Strategy + Registry shape as `briar agent`):

  * `build`   — fetch a board, synthesise per-card scope + deps, sort,
                optionally cascade-chain the branches, and persist.
  * `show`    — pretty-print the stored plan markdown.
  * `next`    — print the next card whose deps are all satisfied. The
                engineer agent reads this to know what to pick up next.
  * `advance` — mark one card done (defaults to the next pending one).
  * `list`    — enumerate stored plans by name.
  * `clear`   — remove a stored plan.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import ClassVar, Dict, List, Optional

from briar._registry import build_registry
from briar.agent._llms import LLMRegistry, make_llm
from briar.commands._enums import ExitCode
from briar.commands.base import Command, confirm
from briar.errors import CliError
from briar.formatting import render
from briar.journal import record, session
from briar.plan._enums import PlanCardStatus
from briar.plan import (
    ImplementationPlan,
    PlanCard,
    build_plan,
    delete_plan,
    list_plans,
    load_plan,
    render_markdown,
    save_plan,
)
from briar.storage import KNOWLEDGE_STORE_NAMES, KnowledgeStore, make_store


log = logging.getLogger(__name__)


# ─── PlanOp Strategy + Registry ─────────────────────────────────────────────


class PlanOp:
    """One `briar plan` subcommand."""

    name: ClassVar[str] = ""
    help: ClassVar[str] = ""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:  # pragma: no cover - abstract
        raise NotImplementedError


# Common --store / --root flags reused by every op.
def _add_store_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--store",
        default="file",
        choices=list(KNOWLEDGE_STORE_NAMES),
        help="KnowledgeStore backend used to persist the plan (default: file)",
    )
    parser.add_argument(
        "--root",
        default="./knowledge",
        help="Local file root (only used when --store=file)",
    )
    parser.add_argument(
        "--company",
        default="",
        help="Company key — used by the postgres store for DSN resolution "
        "and by tracker providers for per-company credentials.",
    )


class BuildOp(PlanOp):
    name = "build"
    help = "Fetch a tracker board and persist an ordered implementation plan."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "board",
            help="Board URL (Jira board, GitHub Projects v2) or short form (jira:KEY).",
        )
        parser.add_argument(
            "--name",
            default="",
            help="Plan name. Defaults to a slug derived from the board URL.",
        )
        parser.add_argument(
            "--cascade",
            action="store_true",
            help="Chain branches: each card's branch_parent is its latest dep's "
            "branch instead of the repository's default branch.",
        )
        parser.add_argument(
            "--default-branch",
            default="main",
            help="Branch the first card (and every non-cascade card) branches from.",
        )
        parser.add_argument(
            "--max-cards",
            type=int,
            default=50,
            help="Cap on cards pulled from the board (default: 50).",
        )
        parser.add_argument(
            "--llm",
            default="",
            choices=[""] + list(LLMRegistry.kinds()),
            help="LLM provider for per-card synthesis. Empty = heuristics only.",
        )
        parser.add_argument(
            "--model",
            default="",
            help="Override the LLM provider's default model (when --llm is set).",
        )
        parser.add_argument(
            "--with-knowledge",
            action="store_true",
            help="Splice the company's knowledge blob (knowledge:<company>) and "
            "active-tickets blob into each card's synthesis context.",
        )
        parser.add_argument(
            "--print",
            action="store_true",
            help="After building, print the plan markdown to stdout.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Build the plan but do NOT persist it. Implies --print.",
        )
        _add_store_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        llm = None
        if args.llm:
            llm = make_llm(args.llm, model=args.model or "")
            if not llm.is_available():
                log.warning("plan: LLM provider %s is not available — falling back to heuristics", args.llm)
                llm = None

        context_sections: List[str] = []
        if args.with_knowledge and args.company:
            context_sections = plan_cmd._gather_knowledge(store, args.company)

        plan = build_plan(
            board_url=args.board,
            name=args.name or plan_cmd._slug_from_url(args.board),
            company=args.company,
            cascade=args.cascade,
            default_branch=args.default_branch,
            max_cards=args.max_cards,
            llm=llm,
            context_sections=context_sections,
        )

        if args.dry_run:
            sys.stdout.write(render_markdown(plan))
            sys.stdout.write("\n")
            return ExitCode.OK

        blob = save_plan(store, plan)
        render(
            {
                "plan": plan.name,
                "blob": blob,
                "cards": len(plan.cards),
                "cascade": plan.cascade,
                "tracker": plan.tracker,
                "project": plan.project,
            },
            args.format,
        )
        if args.print:
            sys.stdout.write("\n")
            sys.stdout.write(render_markdown(plan))
            sys.stdout.write("\n")
        return ExitCode.OK


class ShowOp(PlanOp):
    name = "show"
    help = "Print the markdown body of a stored plan."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("name", help="Plan name (the slug used at build time).")
        _add_store_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        plan = load_plan(store, args.name)
        sys.stdout.write(render_markdown(plan))
        sys.stdout.write("\n")
        return ExitCode.OK


class NextOp(PlanOp):
    name = "next"
    help = "Print the next pending card whose dependencies are all done."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("name", help="Plan name.")
        _add_store_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        plan = load_plan(store, args.name)
        card = plan.next_pending()
        if card is None:
            render({"status": "complete", "plan": plan.name}, args.format)
            return ExitCode.OK
        render(plan_cmd._card_to_dict(plan, card), args.format)
        return ExitCode.OK


class AdvanceOp(PlanOp):
    name = "advance"
    help = "Mark a card as done (defaults to the next pending card)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("name", help="Plan name.")
        parser.add_argument(
            "--card",
            default="",
            help="Card key to mark done. Defaults to the next pending card.",
        )
        parser.add_argument(
            "--status",
            default="done",
            choices=["pending", "in_progress", "done", "blocked"],
            help="Status to set (default: done).",
        )
        _add_store_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        plan = load_plan(store, args.name)
        target_key = args.card.strip()
        if not target_key:
            card = plan.next_pending()
            if card is None:
                raise CliError(f"no pending cards left in plan {plan.name!r}")
            target_key = card.key
        target: Optional[PlanCard] = next((c for c in plan.cards if c.key == target_key), None)
        if target is None:
            raise CliError(f"card {target_key!r} not in plan {plan.name!r}")
        target.status = PlanCardStatus(args.status)
        save_plan(store, plan)
        render(plan_cmd._card_to_dict(plan, target), args.format)
        return ExitCode.OK


class ListOp(PlanOp):
    name = "list"
    help = "List stored plans."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        _add_store_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        items = [{"name": name} for name in sorted(list_plans(store))]
        render(items, args.format, ["name"])
        return ExitCode.OK


class ClearOp(PlanOp):
    name = "clear"
    help = "Remove a stored plan."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("name", help="Plan name.")
        parser.add_argument("--yes", action="store_true", help="Skip confirmation.")
        _add_store_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        if not args.yes and not confirm(f"Delete plan {args.name!r}? [y/N] "):
            print("aborted")
            return ExitCode.GENERAL_ERROR
        removed = delete_plan(store, args.name)
        print(f"{'deleted' if removed else 'not found'} {args.name}")
        return ExitCode.OK if removed else ExitCode.GENERAL_ERROR


class RunOp(PlanOp):
    name = "run"
    help = "Iterate the plan: for each pending card, run `agent implement` then advance."

    # ── Loop control ────────────────────────────────────────────────────
    # `--limit` caps how many cards this invocation will process — useful
    # for a smoke run against one card before letting the loop go wide.
    # `--continue-on-failure` flips the default "stop on first failure"
    # discipline; when set, failed cards are marked `blocked` and the
    # loop keeps moving so a long batch can make partial progress.

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("name", help="Plan name (the slug used at build time).")
        # Loop-specific:
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Stop after N cards (0 = unlimited).",
        )
        parser.add_argument(
            "--continue-on-failure",
            action="store_true",
            help="On implement failure, mark card blocked and continue. Default: stop.",
        )
        # Per-card implement args — same shape as `briar agent implement`
        # minus --ticket-key / --ticket-project (those come from the card
        # / the operator-supplied --tracker-project) and --company (added
        # by `_add_store_arguments` below; validated as required in run()
        # because `implement` needs it for credential resolution).
        parser.add_argument("--owner", required=True, help="Repository owner (GitHub) or workspace (Bitbucket).")
        parser.add_argument("--repo", required=True, help="Repository name / slug.")
        parser.add_argument(
            "--tracker-project",
            default="",
            help="Tracker project key passed to `agent implement` (Jira: PROJ; Linear: ENG; GH/BB: owner/repo). Defaults to <owner>/<repo>.",
        )
        parser.add_argument("--tracker", default="github-issues", help="Tracker provider (default: github-issues).")
        parser.add_argument("--provider", default="github", help="Repository provider (default: github).")
        parser.add_argument("--model", default="", help="Anthropic model override.")
        parser.add_argument("--max-iter", type=int, default=0, help="Iteration ceiling per card.")
        parser.add_argument("--git-user-name", default="")
        parser.add_argument("--git-user-email", default="")
        parser.add_argument("--keep-worktree", action="store_true")
        parser.add_argument("--dry-run", action="store_true", help="Propagate --dry-run to every implement call.")
        parser.add_argument("--runbook", default="", help="Runbook YAML for this company's messages block.")
        parser.add_argument("--knowledge", default="./knowledge", help="File-store root for `agent implement` (postgres ignores).")
        # Meeting-context flags are accepted but defaulted off — implement
        # reads them with `getattr(..., default)` so we only need to set
        # them when explicitly overriding.
        parser.add_argument("--meeting", default="fireflies")
        parser.add_argument("--meeting-key", default="")
        parser.add_argument("--meeting-query", default="")
        parser.add_argument("--meeting-top-k", type=int, default=3)
        parser.add_argument("--meeting-max-bytes", type=int, default=50_000)
        _add_store_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        # Local import keeps the agent module out of plan's import-time
        # graph (agent.py pulls in PyGithub + boto3 + anthropic etc.).
        from briar.commands.agent import run_implement

        if not (args.company or "").strip():
            raise CliError("--company is required for `briar plan run` (agent implement needs it for credential resolution)")

        store = plan_cmd._open_store(args)
        plan = load_plan(store, args.name)

        tracker_project = (args.tracker_project or f"{args.owner}/{args.repo}").strip()
        target = f"{args.owner}/{args.repo}"

        outcomes: Dict[str, int] = {"done": 0, "blocked": 0, "skipped": 0}
        processed = 0

        with session(command="plan.run", target=f"{plan.name}@{target}"):
            record(
                "plan.run.start",
                value={"plan": plan.name, "target": target, "cascade": plan.cascade},
                rationale="loop entry",
            )

            while True:
                if args.limit and processed >= args.limit:
                    record("plan.run.stopped", value="limit_reached", rationale=f"--limit={args.limit}")
                    break
                card = plan.next_pending()
                if card is None:
                    record("plan.run.completed", value="all_done")
                    break

                processed += 1
                impl_args = self._build_implement_args(args, card, tracker_project)
                record(
                    "plan.run.card.start",
                    value=card.key,
                    rationale=f"deps satisfied; branch_parent={card.branch_parent or '(default)'}",
                    artifacts={"branch_name": card.branch_name, "summary": (card.summary or "")[:200]},
                )

                try:
                    rc = run_implement(impl_args)
                except Exception:  # noqa: BLE001 — surface the failure into the journal + plan
                    log.exception("plan run: implement raised for card=%s", card.key)
                    rc = 1

                if rc == 0:
                    card.status = PlanCardStatus.DONE
                    outcomes["done"] += 1
                    record("plan.run.card.completed", value=card.key, rationale="implement rc=0")
                else:
                    card.status = PlanCardStatus.BLOCKED
                    outcomes["blocked"] += 1
                    record("plan.run.card.failed", value=card.key, rationale=f"implement rc={rc}")
                save_plan(store, plan)

                if rc != 0 and not args.continue_on_failure:
                    record(
                        "plan.run.stopped",
                        value="first_failure",
                        rationale="--continue-on-failure not set",
                    )
                    self._render_summary(args, plan, outcomes, stopped_early=True)
                    return rc

            self._render_summary(args, plan, outcomes, stopped_early=False)
            return ExitCode.OK if outcomes["blocked"] == 0 else ExitCode.GENERAL_ERROR

    @staticmethod
    def _build_implement_args(args: argparse.Namespace, card: PlanCard, tracker_project: str) -> argparse.Namespace:
        """Translate the run-loop args + one plan card → an
        `agent implement` argparse.Namespace. Adapter pattern: this
        method is the single seam between the plan-loop model and the
        implement op's contract."""
        impl = argparse.Namespace()
        impl.company = args.company
        impl.owner = args.owner
        impl.repo = args.repo
        impl.ticket_project = tracker_project
        impl.ticket_key = card.key
        impl.tracker = args.tracker
        impl.provider = args.provider
        impl.store = args.store
        impl.knowledge = args.knowledge
        impl.model = args.model
        impl.max_iter = args.max_iter
        impl.git_user_name = args.git_user_name
        impl.git_user_email = args.git_user_email
        impl.keep_worktree = args.keep_worktree
        impl.dry_run = args.dry_run
        impl.runbook = args.runbook
        impl.meeting = args.meeting
        impl.meeting_key = args.meeting_key
        impl.meeting_query = args.meeting_query
        impl.meeting_top_k = args.meeting_top_k
        impl.meeting_max_bytes = args.meeting_max_bytes
        # Carry format/verbose through so any inner `render(...)` call
        # honours the parent invocation's --format choice.
        impl.format = getattr(args, "format", "table")
        impl.verbose = getattr(args, "verbose", False)
        return impl

    @staticmethod
    def _render_summary(args: argparse.Namespace, plan: ImplementationPlan, outcomes: Dict[str, int], stopped_early: bool) -> None:
        render(
            {
                "plan": plan.name,
                "done": outcomes["done"],
                "blocked": outcomes["blocked"],
                "stopped_early": stopped_early,
                "remaining_pending": sum(1 for c in plan.cards if c.status == PlanCardStatus.PENDING),
            },
            args.format,
        )


PLAN_OPS: Dict[str, PlanOp] = build_registry(
    (BuildOp(), ShowOp(), NextOp(), AdvanceOp(), ListOp(), ClearOp(), RunOp()),
    kind="plan op",
)


# ─── CommandPlan ────────────────────────────────────────────────────────────


class CommandPlan(Command):
    name = "plan"
    help = "Build and consume sequenced implementation plans from a tracker board."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="plan_op", required=True, metavar="OP")
        for op in PLAN_OPS.values():
            op_parser = sub.add_parser(op.name, help=op.help)
            op.add_arguments(op_parser)

    def run(self, args: argparse.Namespace) -> int:
        op = PLAN_OPS.get(args.plan_op)
        if op is None:
            known = ", ".join(sorted(PLAN_OPS.keys()))
            log.error("unknown plan op: %s (known: %s)", args.plan_op, known)
            return ExitCode.USAGE_ERROR
        return op.run(self, args)

    # ─── shared helpers ─────────────────────────────────────────────────

    @staticmethod
    def _open_store(args: argparse.Namespace) -> KnowledgeStore:
        return make_store(args.store, file_root=Path(args.root))

    @staticmethod
    def _slug_from_url(url: str) -> str:
        """Derive a default plan name from the board URL — last two
        path segments squashed into a slug. `…/projects/KAN/boards/34`
        becomes `kan-34`."""
        head, _, tail = (url or "").rstrip("/").rpartition("/")
        prev = head.rpartition("/")[2] if head else ""
        parts = [p for p in (prev, tail) if p]
        slug = "-".join(parts).lower()
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        return slug or "plan"

    @staticmethod
    def _card_to_dict(plan: ImplementationPlan, card: PlanCard) -> dict:
        data = card.to_dict()
        data["plan"] = plan.name
        data["board_url"] = plan.board_url
        return data

    @staticmethod
    def _gather_knowledge(store: KnowledgeStore, company: str) -> List[str]:
        """Pull whatever the operator already extracted for this
        company. Best-effort — missing blobs degrade silently."""
        sections: List[str] = []
        for name in (f"knowledge:{company}", f"active-tickets:{company}", f"active-work:{company}"):
            try:
                body = store.get(name)
            except Exception:  # noqa: BLE001
                body = ""
            if body:
                sections.append(f"## {name}\n\n{body}")
        return sections
