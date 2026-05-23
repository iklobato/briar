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
from briar.commands.base import Command, confirm
from briar.errors import CliError
from briar.formatting import render
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
            return 0

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
        return 0


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
        return 0


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
            return 0
        render(plan_cmd._card_to_dict(plan, card), args.format)
        return 0


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
        target.status = args.status
        save_plan(store, plan)
        render(plan_cmd._card_to_dict(plan, target), args.format)
        return 0


class ListOp(PlanOp):
    name = "list"
    help = "List stored plans."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        _add_store_arguments(parser)

    def run(self, plan_cmd: "CommandPlan", args: argparse.Namespace) -> int:
        store = plan_cmd._open_store(args)
        items = [{"name": name} for name in sorted(list_plans(store))]
        render(items, args.format, ["name"])
        return 0


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
            return 1
        removed = delete_plan(store, args.name)
        print(f"{'deleted' if removed else 'not found'} {args.name}")
        return 0 if removed else 1


PLAN_OPS: Dict[str, PlanOp] = build_registry(
    (BuildOp(), ShowOp(), NextOp(), AdvanceOp(), ListOp(), ClearOp()),
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
            return 2
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
