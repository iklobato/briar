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
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import typer

from briar._registry import build_registry
from briar.agent._llm import LLMProvider
from briar.agent._llms import AnthropicLLM, make_llm
from briar.commands._enums import ExitCode, JournalStoreChoice, KnowledgeStoreChoice, LlmChoice, ProviderChoice, TrackerChoice
from briar.commands.base import CommandOptions, EnrichmentOptions, Route, Subcommand, SubcommandCommand, confirm, normalize_owner_repo
from briar.errors import CliError
from briar.formatting import render
from briar.journal import make_journal_store, record, session
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
from briar.storage import KnowledgeStore, default_store_kind, make_store

log = logging.getLogger(__name__)


# ─── PlanOp Strategy + Registry ─────────────────────────────────────────────


class PlanOp(Subcommand):
    """One `briar plan` subcommand."""


def _keep_plan_in_memory(store: KnowledgeStore, plan: ImplementationPlan) -> str:
    """`save_plan` stand-in for `plan run --dry-run`: writes nothing."""
    return ""


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

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        plan = load_plan(store, args.name)
        sys.stdout.write(render_markdown(plan))
        sys.stdout.write("\n")
        return ExitCode.OK


class StatusOp(PlanOp):
    name = "status"
    help = "Show past / current / to-be-done cards with journal artifacts."

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

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        items = [{"name": name} for name in sorted(list_plans(store))]
        render(items, args.format, ["name"])
        return ExitCode.OK


class ClearOp(PlanOp):
    name = "clear"
    help = "Remove a stored plan."

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

        # --dry-run walks the plan in memory only: card statuses still move so
        # the loop shows which cards it would pick, but the saved plan, the
        # knowledge writeback and the `plan.run` history that a later real run
        # (and `plan status`) reads all stay as they were. The session is
        # still journaled, under a name outside that `plan.run` prefix.
        persist_plan = _keep_plan_in_memory if args.dry_run else save_plan
        session_command = "plan.dry-run" if args.dry_run else "plan.run"

        with session(command=session_command, target=f"{plan.name}@{target}"):
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
                    persist_plan(store, plan)
                    replans += 1
                    outcomes["replans"] = replans
                    continue

                # PICK
                card = next((c for c in plan.cards if c.key == decision.key), None)
                if card is None:
                    raise CliError(f"selector picked unknown card {decision.key!r}")
                if decision.branch_parent:
                    card.branch_parent = decision.branch_parent
                else:
                    card.branch_parent = self._stacked_parent(plan, card) or card.branch_parent

                processed += 1
                card.status = PlanCardStatus.IN_PROGRESS
                persist_plan(store, plan)
                impl_args = self._build_implement_args(
                    args,
                    card,
                    tracker_project,
                    default_branch=plan.default_branch,
                    optional_parents=self._done_dependency_branches(plan, card),
                    plan_notes=self._plan_notes(store, plan),
                )
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
                    persist_plan(store, plan)
                    if not args.dry_run:
                        try:
                            writer.write(store=store, plan=plan, card=card, diff="")
                        except Exception:  # noqa: BLE001: writeback never blocks the loop
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
                    persist_plan(store, plan)

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
    def _done_dependency_branches(plan: ImplementationPlan, card: PlanCard) -> List[str]:
        """Branches of this card's dependencies that are done, in
        `depends_on` order."""
        by_key = {c.key: c for c in plan.cards}
        deps = [by_key.get(key) for key in card.depends_on]
        return [d.branch_name for d in deps if d is not None and d.status == PlanCardStatus.DONE and d.branch_name]

    @classmethod
    def _stacked_parent(cls, plan: ImplementationPlan, card: PlanCard) -> str:
        """The branch to stack on when the selector names none: the first
        done dependency's branch, so the card starts from code its
        dependency already wrote. Empty when no dependency is done."""
        branches = cls._done_dependency_branches(plan, card)
        return branches[0] if branches else ""

    @staticmethod
    def _plan_notes(store, plan: ImplementationPlan) -> str:
        """The plan's notes blob, re-read per card so the writeback from
        the previous card is included."""
        if not (plan.company and plan.name):
            return ""
        return store.get(f"knowledge:{plan.company}.{plan.name}") or ""

    @staticmethod
    def _build_implement_args(
        args: argparse.Namespace,
        card: PlanCard,
        tracker_project: str,
        *,
        default_branch: str = "main",
        optional_parents: Iterable[str] = (),
        plan_notes: str = "",
    ) -> argparse.Namespace:
        """Translate the run-loop args + one plan card → an
        `agent implement` argparse.Namespace. Adapter pattern.

        The card's branch fields steer the clone and the PR: a parent
        that is another card's branch stacks this card on it. A parent
        equal to the plan's `default_branch` may fall back to the repo's
        real default branch (plans default to `main`)."""
        impl = argparse.Namespace()
        impl.company = args.company
        impl.owner = args.owner
        impl.repo = args.repo
        impl.ticket_project = tracker_project
        impl.ticket_key = card.key
        impl.branch_name = card.branch_name
        impl.base_branch = card.branch_parent
        # A parent that is the plan default or a done dependency's branch
        # may be missing (repo default is `master`; branch deleted after
        # merge): fall back to the repo's default branch then.
        impl.base_branch_optional = card.branch_parent == default_branch or card.branch_parent in set(optional_parents)
        impl.plan_notes = plan_notes
        impl.tracker = args.tracker
        impl.provider = args.provider
        impl.store = args.store
        # One root: the per-card implement reuses the plan's `--root`.
        impl.knowledge = args.root
        # The per-card agent always runs on Anthropic; a `--model` meant for
        # another planner LLM (e.g. `gpt-4o`) would make every card fail.
        impl.model = args.model if args.llm == AnthropicLLM.kind else ""
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
        impl.chat = args.chat
        impl.slack_query = args.slack_query
        impl.slack_top_k = args.slack_top_k
        impl.slack_max_bytes = args.slack_max_bytes
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


# ─── Options on Typer ──────────────────────────────────────────────

DEFAULT_MAX_CARDS = 50
DEFAULT_MAX_REPLANS = 3


@dataclass(frozen=True)
class PlanStoreOptions(CommandOptions):
    """Where every plan operation reads and writes the plan. The `--store` default
    (postgres when BRIAR_DATABASE_URL is set) is read when the command runs, as in 1.x."""

    UNSET_AS = {"store": default_store_kind}

    store: Optional[KnowledgeStoreChoice] = typer.Option(
        None, "--store", help="KnowledgeStore backend used to persist the plan (default: postgres if BRIAR_DATABASE_URL set, else file)"
    )
    root: str = typer.Option("./knowledge", "--root", help="Local file root (only used when --store=file)")
    company: str = typer.Option(
        "",
        "--company",
        help="Company key: used by the postgres store for DSN resolution, by tracker providers for per-company credentials, "
        "and to namespace the plan-scoped knowledge blob (`knowledge:<company>.<plan>`).",
    )


@dataclass(frozen=True)
class PlanJournalOptions(PlanStoreOptions):
    journal_store: JournalStoreChoice = typer.Option(
        JournalStoreChoice.FILE, "--journal-store", help="JournalStore backend used to read past run decisions (default: file)."
    )
    journal_root: str = typer.Option("./journal", "--journal-root", help="Local journal root (only used when --journal-store=file).")


@dataclass(frozen=True)
class PlanBuildOptions(PlanStoreOptions):
    ROUTE = Route(CommandPlan.name, CommandPlan.dest, BuildOp.name)
    UNSET_AS = {"store": default_store_kind, "llm": str}

    board: str = typer.Argument(..., help="Board URL (Jira board, GitHub Projects v2) or short form (jira:KEY).")
    name: str = typer.Option("", "--name", help="Plan name. Defaults to a slug derived from the board URL.")
    default_branch: str = typer.Option("main", "--default-branch", help="Branch each card branches from by default. The LLM selector may override per pick.")
    max_cards: int = typer.Option(DEFAULT_MAX_CARDS, "--max-cards", help="Cap on cards pulled from the board (default: 50).")
    with_knowledge: bool = typer.Option(
        False,
        "--with-knowledge",
        help="Splice the company's knowledge blob (knowledge:<company>) and active-tickets blob into each card's synthesis context.",
    )
    print: bool = typer.Option(False, "--print", help="After building, print the plan markdown to stdout.")
    dry_run: bool = typer.Option(False, "--dry-run", help="Build the plan but do NOT persist it. Implies --print.")
    llm: Optional[LlmChoice] = typer.Option(None, "--llm", help="LLM provider. Empty = heuristics-only.")
    model: str = typer.Option("", "--model", help="Override the LLM provider's default model.")


@dataclass(frozen=True)
class PlanShowOptions(PlanStoreOptions):
    ROUTE = Route(CommandPlan.name, CommandPlan.dest, ShowOp.name)

    name: str = typer.Argument(..., help="Plan name (the slug used at build time).")


@dataclass(frozen=True)
class PlanStatusOptions(PlanJournalOptions):
    ROUTE = Route(CommandPlan.name, CommandPlan.dest, StatusOp.name)

    name: str = typer.Argument(..., help="Plan name.")


@dataclass(frozen=True)
class PlanNextOptions(PlanJournalOptions):
    ROUTE = Route(CommandPlan.name, CommandPlan.dest, NextOp.name)

    name: str = typer.Argument(..., help="Plan name.")
    llm: LlmChoice = typer.Option(..., "--llm", help="LLM provider. Required.")
    model: str = typer.Option("", "--model", help="Override the LLM provider's default model.")


@dataclass(frozen=True)
class PlanAdvanceOptions(PlanStoreOptions):
    ROUTE = Route(CommandPlan.name, CommandPlan.dest, AdvanceOp.name)

    name: str = typer.Argument(..., help="Plan name.")
    card: str = typer.Option(..., "--card", help="Card key to mark.")
    status: PlanCardStatus = typer.Option(PlanCardStatus.DONE, "--status", help="Status to set (default: done).")


@dataclass(frozen=True)
class PlanListOptions(PlanStoreOptions):
    ROUTE = Route(CommandPlan.name, CommandPlan.dest, ListOp.name)


@dataclass(frozen=True)
class PlanClearOptions(PlanStoreOptions):
    ROUTE = Route(CommandPlan.name, CommandPlan.dest, ClearOp.name)

    name: str = typer.Argument(..., help="Plan name.")
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation.")


@dataclass(frozen=True)
class PlanRunOptions(PlanJournalOptions, EnrichmentOptions):
    """The per-card `agent implement` reuses the plan's `--root` as its file-store root
    (one root, not two), so there is no `--knowledge` here."""

    ROUTE = Route(CommandPlan.name, CommandPlan.dest, RunOp.name)

    meeting_query: str = typer.Option("", "--meeting-query", help="Keyword search across recent meetings (defaults to the card key).")
    slack_query: str = typer.Option("", "--slack-query", help="Keyword search across Slack threads (defaults to the card key).")
    name: str = typer.Argument(..., help="Plan name (the slug used at build time).")
    limit: int = typer.Option(0, "--limit", help="Stop after N cards (0 = unlimited).")
    continue_on_failure: bool = typer.Option(False, "--continue-on-failure", help="On implement failure, mark card blocked and continue. Default: stop.")
    max_replans: int = typer.Option(DEFAULT_MAX_REPLANS, "--max-replans", help="Cap on selector REPLAN actions per invocation (default: 3).")
    owner: str = typer.Option("", "--owner", help="Repository owner (GitHub) or workspace (Bitbucket). Inferred from git if omitted.")
    repo: str = typer.Option("", "--repo", help="Repository as `owner/repo`, or a bare name with --owner. Inferred from git if omitted.")
    tracker_project: str = typer.Option("", "--tracker-project", help="Tracker project key passed to `agent implement`. Defaults to <owner>/<repo>.")
    tracker: TrackerChoice = typer.Option(TrackerChoice.GITHUB_ISSUES, "--tracker", help="Tracker provider (default: github-issues).")
    provider: ProviderChoice = typer.Option(ProviderChoice.GITHUB, "--provider", help="Repository provider (default: github).")
    max_iter: int = typer.Option(0, "--max-iter", help="Iteration ceiling per card.")
    git_user_name: str = typer.Option("", "--git-user-name")
    git_user_email: str = typer.Option("", "--git-user-email")
    keep_worktree: bool = typer.Option(False, "--keep-worktree")
    dry_run: bool = typer.Option(False, "--dry-run", help="Pass --dry-run to every implement call and keep the plan, its notes and its run history unchanged.")
    runbook: str = typer.Option("", "--runbook", help="Runbook YAML for this company's messages block.")
    llm: LlmChoice = typer.Option(..., "--llm", help="LLM provider. Required.")
    model: str = typer.Option("", "--model", help="Override the LLM provider's default model.")
