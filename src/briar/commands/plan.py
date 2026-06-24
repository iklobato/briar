"""`briar plan` — build / inspect / advance LLM-driven implementation
plans built from tracker boards.

Subcommands (same Strategy + Registry shape as `briar agent`):

  * `build`   — fetch a board, synthesise per-card scope + deps,
                persist the plan, and seed `knowledge:<company>.<plan>`.
  * `show`    — pretty-print the stored plan markdown.
  * `status`  — list past / current / to-be-done with journal artifacts
                (commit shas, PR URLs, failure rationales).
  * `next`    — ask the LLM selector what to do next; print the decision.
  * `advance` — mark a card with a chosen status. `--card` is required;
                there is no auto-pick (the LLM selector owns that now).
  * `list`    — enumerate stored plans by name.
  * `clear`   — remove a stored plan.
  * `run`     — iterate the LLM selector: pick → implement → writeback,
                with `replan` as a first-class action.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

from briar._registry import build_registry
from briar.agent._llm import LLMProvider
from briar.agent._llms import LLMRegistry, make_llm
from briar.commands._enums import ExitCode
from briar.commands.base import Subcommand, SubcommandCommand, confirm, normalize_owner_repo
from briar.errors import CliError
from briar.extract._meeting import DEFAULT_MEETING_MAX_BYTES, DEFAULT_MEETING_TOP_K
from briar.extract._providers import PROVIDERS
from briar.extract._trackers import TRACKERS
from briar.formatting import render
from briar.journal import JOURNAL_STORE_NAMES, make_journal_store, record, session
from briar.plan import (
    ImplementationPlan,
    KnowledgeWriter,
    PlanCard,
    PlanContext,
    Selector,
    SelectorActionKind,
    SelectorDecision,
    build_plan,
    collect_status,
    delete_plan,
    list_plans,
    load_plan,
    render_markdown,
    render_plan_knowledge,
    render_table,
    replan,
    save_plan,
)
from briar.plan._enums import PlanCardStatus
from briar.storage import KNOWLEDGE_STORE_NAMES, KnowledgeStore, default_store_kind, make_store

log = logging.getLogger(__name__)


# ─── PlanOp Strategy + Registry ─────────────────────────────────────────────


class PlanOp(Subcommand):
    """One `briar plan` subcommand."""


def _add_store_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--store",
        default=default_store_kind(),
        choices=list(KNOWLEDGE_STORE_NAMES),
        help="KnowledgeStore backend used to persist the plan (default: postgres if BRIAR_DATABASE_URL set, else file)",
    )
    parser.add_argument(
        "--root",
        default="./knowledge",
        help="Local file root (only used when --store=file)",
    )
    parser.add_argument(
        "--company",
        default="",
        help="Company key — used by the postgres store for DSN resolution, "
        "by tracker providers for per-company credentials, and to namespace "
        "the plan-scoped knowledge blob (`knowledge:<company>.<plan>`).",
    )


def _add_journal_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--journal-store",
        default="file",
        choices=list(JOURNAL_STORE_NAMES),
        help="JournalStore backend used to read past run decisions (default: file).",
    )
    parser.add_argument(
        "--journal-root",
        default="./journal",
        help="Local journal root (only used when --journal-store=file).",
    )


def _add_llm_arguments(parser: argparse.ArgumentParser, *, required: bool) -> None:
    parser.add_argument(
        "--llm",
        default="",
        required=required,
        choices=list(LLMRegistry.kinds()) if required else ([""] + list(LLMRegistry.kinds())),
        help="LLM provider. " + ("Required." if required else "Empty = heuristics-only."),
    )
    parser.add_argument(
        "--model",
        default="",
        help="Override the LLM provider's default model.",
    )


def _require_llm(args: argparse.Namespace, *, op: str) -> LLMProvider:
    if not args.llm:
        raise CliError(f"--llm is required for `briar plan {op}` (LLM-driven planning)")
    llm = make_llm(args.llm, model=args.model or "")
    if not llm.is_available():
        raise CliError(f"--llm={args.llm} is not available (missing credentials?)")
    return llm


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
            "--default-branch",
            default="main",
            help="Branch each card branches from by default. The LLM selector may " "override per pick.",
        )
        parser.add_argument(
            "--max-cards",
            type=int,
            default=50,
            help="Cap on cards pulled from the board (default: 50).",
        )
        parser.add_argument(
            "--with-knowledge",
            action="store_true",
            help="Splice the company's knowledge blob (knowledge:<company>) and " "active-tickets blob into each card's synthesis context.",
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
        _add_llm_arguments(parser, required=False)
        _add_store_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        llm: Optional[LLMProvider] = None
        if args.llm:
            llm = make_llm(args.llm, model=args.model or "")
            if not llm.is_available():
                log.warning("plan build: LLM provider %s is not available — falling back to heuristics", args.llm)
                llm = None

        context_sections: List[str] = []
        if args.with_knowledge and args.company:
            context_sections = plan_cmd._gather_knowledge(store, args.company)

        plan = build_plan(
            board_url=args.board,
            name=args.name or plan_cmd._slug_from_url(args.board),
            company=args.company,
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
        seed_blob = ""
        if plan.company:
            seed_blob = f"knowledge:{plan.company}.{plan.name}"
            store.put_if_changed(seed_blob, render_plan_knowledge(plan), category="knowledge")

        render(
            {
                "plan": plan.name,
                "blob": blob,
                "knowledge_seed": seed_blob,
                "cards": len(plan.cards),
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


class StatusOp(PlanOp):
    name = "status"
    help = "Show past / current / to-be-done cards with journal artifacts."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("name", help="Plan name.")
        _add_store_arguments(parser)
        _add_journal_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        journal_store = plan_cmd._open_journal_store(args)
        plan = load_plan(store, args.name)
        snapshot = collect_status(plan, journal_store)
        fmt = getattr(args, "format", "table")
        if fmt == "table":
            sys.stdout.write(render_table(snapshot))
        else:
            render(snapshot, fmt)
        return ExitCode.OK


class NextOp(PlanOp):
    name = "next"
    help = "Ask the LLM selector what to do next; print the decision."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("name", help="Plan name.")
        _add_llm_arguments(parser, required=True)
        _add_store_arguments(parser)
        _add_journal_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        journal_store = plan_cmd._open_journal_store(args)
        plan = load_plan(store, args.name)
        llm = _require_llm(args, op="next")
        ctx = PlanContext.from_stores(journal_store=journal_store, knowledge_store=store, plan=plan)
        decision = Selector(llm).pick(plan, ctx)
        render(plan_cmd._decision_to_dict(plan, decision), args.format)
        return ExitCode.OK


class AdvanceOp(PlanOp):
    name = "advance"
    help = "Mark a card with a chosen status. --card is required."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("name", help="Plan name.")
        parser.add_argument(
            "--card",
            required=True,
            help="Card key to mark.",
        )
        parser.add_argument(
            "--status",
            default="done",
            choices=[s.value for s in PlanCardStatus],
            help="Status to set (default: done).",
        )
        _add_store_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        plan = load_plan(store, args.name)
        target_key = args.card.strip()
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
    help = "Iterate the LLM selector: pick → implement → writeback, with replan."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("name", help="Plan name (the slug used at build time).")
        # Loop control.
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
        parser.add_argument(
            "--max-replans",
            type=int,
            default=3,
            help="Cap on selector REPLAN actions per invocation (default: 3).",
        )
        # Per-card implement args.
        parser.add_argument("--owner", default="", help="Repository owner (GitHub) or workspace (Bitbucket). Inferred from git if omitted.")
        parser.add_argument("--repo", default="", help="Repository as `owner/repo`, or a bare name with --owner. Inferred from git if omitted.")
        parser.add_argument(
            "--tracker-project",
            default="",
            help="Tracker project key passed to `agent implement`. Defaults to <owner>/<repo>.",
        )
        parser.add_argument("--tracker", default="github-issues", choices=sorted(TRACKERS.keys()), help="Tracker provider (default: github-issues).")
        parser.add_argument("--provider", default="github", choices=sorted(PROVIDERS.keys()), help="Repository provider (default: github).")
        parser.add_argument("--max-iter", type=int, default=0, help="Iteration ceiling per card.")
        parser.add_argument("--git-user-name", default="")
        parser.add_argument("--git-user-email", default="")
        parser.add_argument("--keep-worktree", action="store_true")
        parser.add_argument("--dry-run", action="store_true", help="Propagate --dry-run to every implement call.")
        parser.add_argument("--runbook", default="", help="Runbook YAML for this company's messages block.")
        parser.add_argument(
            "--knowledge",
            default="./knowledge",
            help="File-store root for `agent implement` (postgres ignores).",
        )
        parser.add_argument("--meeting", default="fireflies")
        parser.add_argument("--meeting-key", default="")
        parser.add_argument("--meeting-query", default="")
        parser.add_argument("--meeting-top-k", type=int, default=DEFAULT_MEETING_TOP_K)
        parser.add_argument("--meeting-max-bytes", type=int, default=DEFAULT_MEETING_MAX_BYTES)
        _add_llm_arguments(parser, required=True)
        _add_store_arguments(parser)
        _add_journal_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        from briar.commands.agent import run_implement

        if not (args.company or "").strip():
            raise CliError("--company is required for `briar plan run`")
        normalize_owner_repo(args)

        store = plan_cmd._open_store(args)
        journal_store = plan_cmd._open_journal_store(args)
        plan = load_plan(store, args.name)
        llm = _require_llm(args, op="run")
        selector = Selector(llm)
        writer = KnowledgeWriter(llm)

        tracker_project = (args.tracker_project or f"{args.owner}/{args.repo}").strip()
        target = f"{args.owner}/{args.repo}"

        outcomes: Dict[str, int] = {"done": 0, "blocked": 0, "replans": 0}
        processed = 0
        replans = 0

        with session(command="plan.run", target=f"{plan.name}@{target}"):
            record(
                "plan.run.start",
                value={"plan": plan.name, "target": target},
                rationale="LLM-driven loop entry",
            )

            while True:
                if args.limit and processed >= args.limit:
                    record("plan.run.stopped", value="limit_reached", rationale=f"--limit={args.limit}")
                    break

                ctx = PlanContext.from_stores(journal_store=journal_store, knowledge_store=store, plan=plan)
                try:
                    decision = selector.pick(plan, ctx)
                except CliError as exc:
                    record("plan.run.stopped", value="selector_error", rationale=str(exc))
                    self._render_summary(args, plan, outcomes, stopped_early=True)
                    raise

                record(
                    "plan.next.decision",
                    value=decision.kind.value,
                    rationale=decision.why,
                    artifacts={"key": decision.key} if decision.key else {},
                )

                if decision.kind is SelectorActionKind.COMPLETE:
                    record("plan.run.completed", value="all_done", rationale=decision.why)
                    break
                if decision.kind is SelectorActionKind.BLOCKED:
                    record("plan.run.stopped", value="blocked", rationale=decision.why)
                    self._render_summary(args, plan, outcomes, stopped_early=True)
                    return ExitCode.GENERAL_ERROR
                if decision.kind is SelectorActionKind.REPLAN:
                    if replans >= args.max_replans:
                        record(
                            "plan.run.stopped",
                            value="replan_cap",
                            rationale=f"--max-replans={args.max_replans} reached",
                        )
                        self._render_summary(args, plan, outcomes, stopped_early=True)
                        return ExitCode.GENERAL_ERROR
                    record("plan.replan.requested", value=plan.name, rationale=decision.why)
                    plan = replan(plan, llm=llm)
                    save_plan(store, plan)
                    replans += 1
                    outcomes["replans"] = replans
                    continue

                # PICK
                card = next((c for c in plan.cards if c.key == decision.key), None)
                if card is None:
                    raise CliError(f"selector picked unknown card {decision.key!r}")
                if decision.branch_parent:
                    card.branch_parent = decision.branch_parent

                processed += 1
                card.status = PlanCardStatus.IN_PROGRESS
                save_plan(store, plan)
                impl_args = self._build_implement_args(args, card, tracker_project)
                record(
                    "plan.run.card.start",
                    value=card.key,
                    rationale=f"branch_parent={card.branch_parent or '(default)'}",
                    artifacts={"branch_name": card.branch_name, "summary": (card.summary or "")[:200]},
                )

                rc, exc_msg = self._invoke_implement(run_implement, impl_args, card.key)
                if rc == 0:
                    card.status = PlanCardStatus.DONE
                    card.last_attempt_summary = ""
                    outcomes["done"] += 1
                    record("plan.run.card.completed", value=card.key, rationale="implement rc=0")
                    save_plan(store, plan)
                    try:
                        writer.write(store=store, plan=plan, card=card, diff="")
                    except Exception:  # noqa: BLE001 — writeback never blocks the loop
                        log.exception("plan run: writeback failed for card=%s", card.key)
                else:
                    card.status = PlanCardStatus.BLOCKED
                    card.last_attempt_summary = (exc_msg or f"implement rc={rc}")[:500]
                    outcomes["blocked"] += 1
                    record(
                        "plan.run.card.failed",
                        value=card.key,
                        rationale=f"implement rc={rc}",
                        artifacts={"last_attempt_summary": card.last_attempt_summary},
                    )
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
    def _invoke_implement(run_implement, impl_args, card_key: str):
        try:
            return run_implement(impl_args), ""
        except Exception as exc:  # noqa: BLE001 — surface via journal + plan
            log.exception("plan run: implement raised for card=%s", card_key)
            return int(ExitCode.GENERAL_ERROR), f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _build_implement_args(args: argparse.Namespace, card: PlanCard, tracker_project: str) -> argparse.Namespace:
        """Translate the run-loop args + one plan card → an
        `agent implement` argparse.Namespace. Adapter pattern."""
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
                "replans": outcomes.get("replans", 0),
                "stopped_early": stopped_early,
                "remaining_pending": sum(1 for c in plan.cards if c.status == PlanCardStatus.PENDING),
            },
            args.format,
        )


PLAN_OPS: Dict[str, PlanOp] = build_registry(
    (BuildOp(), ShowOp(), StatusOp(), NextOp(), AdvanceOp(), ListOp(), ClearOp(), RunOp()),
    kind="plan op",
)


# ─── CommandPlan ────────────────────────────────────────────────────────────


class CommandPlan(SubcommandCommand):
    name = "plan"
    help = "Build and consume LLM-driven implementation plans from a tracker board."
    dest = "plan_op"
    op_noun = "plan op"
    ops = PLAN_OPS

    # ─── shared helpers ─────────────────────────────────────────────────

    @staticmethod
    def _open_store(args: argparse.Namespace) -> KnowledgeStore:
        return make_store(args.store, file_root=Path(args.root))

    @staticmethod
    def _open_journal_store(args: argparse.Namespace):
        return make_journal_store(
            getattr(args, "journal_store", "file"),
            file_root=Path(getattr(args, "journal_root", "./journal")),
        )

    @staticmethod
    def _slug_from_url(url: str) -> str:
        """Derive a default plan name from the board URL — last two
        path segments squashed into a slug."""
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
    def _decision_to_dict(plan: ImplementationPlan, decision: SelectorDecision) -> dict:
        data = {
            "plan": plan.name,
            "action": decision.kind.value,
            "key": decision.key,
            "why": decision.why,
            "branch_parent": decision.branch_parent,
        }
        if decision.kind is SelectorActionKind.PICK:
            card = next((c for c in plan.cards if c.key == decision.key), None)
            if card is not None:
                data["title"] = card.title
                data["branch_name"] = card.branch_name
                data["summary"] = card.summary
        return data

    @staticmethod
    def _gather_knowledge(store: KnowledgeStore, company: str) -> List[str]:
        """Pull whatever the operator already extracted for this
        company. Best-effort — missing blobs degrade gracefully but a
        WARNING surfaces so the operator knows the plan is built
        against partial context."""
        sections: List[str] = []
        for name in (f"knowledge:{company}", f"active-tickets:{company}", f"active-work:{company}"):
            try:
                body = store.get(name)
            except Exception as exc:  # noqa: BLE001
                log.warning("plan: knowledge blob %s could not be read (%s); proceeding without it", name, type(exc).__name__)
                body = ""
            if body:
                sections.append(f"## {name}\n\n{body}")
        return sections
