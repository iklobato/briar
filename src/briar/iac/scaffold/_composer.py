"""Compose a config bundle from the four registries.

Shared by every concrete scaffold template (implementation, pr_fixes,
…) so adding a new top-level template only writes the small "which
defaults" delta — sources / archetype / shape / trigger come from the
shared registries."""

from __future__ import annotations

import argparse
import logging
from typing import Any, Dict, List

from briar.errors import ConfigError
from briar.iac.scaffold._knowledge import KnowledgeSplicer
from briar.iac.scaffold.archetypes import ARCHETYPES, AgentArchetype
from briar.iac.scaffold.shapes import WORKFLOW_SHAPES, WorkflowShape
from briar.iac.scaffold.sources import SOURCE_TEMPLATES, SourceTemplate
from briar.iac.scaffold.triggers import TRIGGER_TEMPLATES, TriggerTemplate
from briar.journal import record

log = logging.getLogger(__name__)


class ScaffoldComposer:
    """Builds the JSON bundle out of selected source / archetype /
    shape / trigger registry entries plus argparse-derived knobs."""

    @classmethod
    def compose(cls, args: argparse.Namespace, *, target: str) -> Dict[str, Any]:
        """`target` is the human-readable identifier (e.g.
        acme/widgets) used in the agent backstory."""
        prefix = args.prefix

        source_templates = cls._resolved_sources(args.source)
        record(
            "scaffold.sources",
            value=list(args.source),
            rationale="user-selected source kinds (--source repeatable)",
            alternatives=tuple(sorted(SOURCE_TEMPLATES.keys())),
        )
        archetype: AgentArchetype = cls._resolved(
            args.archetype,
            ARCHETYPES,
            "archetype",
        )
        record(
            "scaffold.archetype",
            value=archetype.name,
            rationale=archetype.description,
            alternatives=tuple(sorted(ARCHETYPES.keys())),
        )
        shape: WorkflowShape = cls._resolved(
            args.shape,
            WORKFLOW_SHAPES,
            "shape",
        )
        record(
            "scaffold.shape",
            value=shape.name,
            rationale="workflow shape — controls human-checkpoint placement",
            alternatives=tuple(sorted(WORKFLOW_SHAPES.keys())),
        )
        trigger_template: TriggerTemplate = cls._resolved(
            args.trigger_kind,
            TRIGGER_TEMPLATES,
            "trigger_kind",
        )
        record(
            "scaffold.trigger",
            value=trigger_template.kind,
            rationale="entry condition for the workflow",
            alternatives=tuple(sorted(TRIGGER_TEMPLATES.keys())),
        )

        sources_block: List[Dict[str, Any]] = []
        tools_block: List[Dict[str, Any]] = []
        for tmpl in source_templates:
            sources_block.append(tmpl.build_source(args, prefix))
            tools_block.extend(tmpl.build_tools(args, prefix))

        unfiltered_count = len(tools_block)
        tools_block = archetype.filter_tools(tools_block)
        record(
            "scaffold.tools.filtered",
            value={"kept": len(tools_block), "dropped": unfiltered_count - len(tools_block)},
            rationale=f"archetype {archetype.name!r} tool_filter applied to source-emitted tools",
            artifacts={"kept_tools": ",".join(t["implementation_ref"] for t in tools_block)},
        )

        persona = archetype.build_persona(target)
        agent_key = f"{prefix}-{archetype.name}"
        # If a `--company` is supplied, splice in the relevant extractor
        # sections from the configured KnowledgeStore. The resulting
        # `system_prompt` carries the actual mined data so downstream
        # agent runtimes don't need to query Postgres themselves.
        system_prompt = cls._knowledge_prologue(args, archetype)
        agent: Dict[str, Any] = {
            "key": agent_key,
            "name": agent_key,
            "role": persona["role"],
            "goal": persona["goal"],
            "backstory": persona["backstory"],
            "system_prompt": system_prompt,
            "llm_model_key": f"{prefix}-model",
            "tool_keys": [t["key"] for t in tools_block],
            "source_keys": [s["key"] for s in sources_block],
            "max_iter": archetype.max_iter,
        }

        workflow_graph = shape.build_graph(agent_key)
        workflow_graph["nodes"] = [cls._append_source_context(node, [s["name"] for s in sources_block]) for node in workflow_graph["nodes"]]

        workflow: Dict[str, Any] = {
            "key": f"{prefix}-workflow",
            "name": f"{prefix}-workflow",
            "description": f"{archetype.description} for {target} ({shape.name})",
            "graph": workflow_graph,
        }

        bundle: Dict[str, Any] = {
            "version": 1,
            "llm_models": [
                {
                    "key": f"{prefix}-model",
                    "name": args.model,
                    "provider_key": args.llm_provider_key,
                    "display_name": args.model,
                    "default_params": {"temperature": 0.2},
                }
            ],
            "sources": sources_block,
            "tools": tools_block,
            "agents": [agent],
            "workflows": [workflow],
        }

        trigger_dict = trigger_template.build_trigger(
            args,
            prefix,
            workflow_key=f"{prefix}-workflow",
        )
        if trigger_dict:
            bundle["triggers"] = [trigger_dict]
        return bundle

    @staticmethod
    def _knowledge_prologue(args: argparse.Namespace, archetype: AgentArchetype) -> str:
        """Build the system_prompt prologue from the configured store.

        Skips silently when `--company` isn't supplied OR when the
        archetype has nothing in its `consumes` list OR when the store
        can't be reached (e.g. dev laptop without BRIAR_DATABASE_URL).
        The agent's backstory already names every consumed extractor,
        so the agent will still know what it ought to read — it just
        won't have the cached content in its prompt."""
        ns = vars(args)
        company = (ns.get("company") or "").strip()
        if not company or not archetype.consumes:
            return ""
        from briar.storage import make_store

        store_name = (ns.get("knowledge_store") or "").strip()
        if not store_name:
            from briar.env_vars import CredEnv

            store_name = "postgres" if CredEnv.BRIAR_DATABASE_URL.read() else "file"
        try:
            store = make_store(store_name)
        except Exception:  # noqa: BLE001
            log.exception("scaffold: could not open store=%s; skipping knowledge splice", store_name)
            return ""
        try:
            splicer = KnowledgeSplicer.from_store(store, company)
            return splicer.prologue(archetype)
        except Exception:  # noqa: BLE001
            log.exception("scaffold: knowledge splice failed for company=%s store=%s", company, store_name)
            return ""

    @staticmethod
    def _resolved_sources(kinds: List[str]) -> List[SourceTemplate]:
        out: List[SourceTemplate] = []
        for kind in kinds:
            tmpl = SOURCE_TEMPLATES.get(kind)
            if tmpl is None:
                known = ", ".join(sorted(SOURCE_TEMPLATES))
                raise ConfigError(f"unknown source kind {kind!r}; known: {known}")
            out.append(tmpl)
        return out

    @staticmethod
    def _resolved(name: str, registry: Dict[str, Any], label: str) -> Any:
        tmpl = registry.get(name)
        if tmpl is None:
            known = ", ".join(sorted(registry))
            raise ConfigError(f"unknown {label} {name!r}; known: {known}")
        return tmpl

    @staticmethod
    def _append_source_context(
        node: Dict[str, Any],
        source_names: List[str],
    ) -> Dict[str, Any]:
        """For agent nodes, append a `{source_<name>}` placeholder block
        to the prompt for every gathered source the agent is bound to.
        The orchestrator's `prompt.format(**context)` then substitutes
        the actual fetched payload at run time."""
        if node.get("kind") != "agent" or not source_names:
            return node
        placeholders = "\n".join(f"## Source `{name}`\n{{source_{name}}}" for name in source_names)
        enriched_prompt = f"{node.get('prompt', '').rstrip()}\n\n" f"--- gathered sources ---\n\n{placeholders}\n"
        out = dict(node)
        out["prompt"] = enriched_prompt
        return out


def target_for(args: argparse.Namespace) -> str:
    """Pick the agent target string from the chosen sources.

    Walks `args.source` in the user's declared order, asks each
    `SourceTemplate.target(args)`, returns the first non-empty
    answer. Falls back to the scaffold's `--prefix` when no source
    declares a target (e.g. AWS-only scaffolds). The prefix path is
    not great — every tracker source should be returning a real
    identifier — but it's better than crashing on `{target}`
    interpolation.

    Was `ScaffoldResolver.target_for` before Phase 12 demoted the
    static-only namespace class to a module function."""
    ns = vars(args)
    kinds: List[str] = list(ns.get("source") or [])
    for kind in kinds:
        tmpl = SOURCE_TEMPLATES.get(kind)
        if tmpl is None:
            continue
        ident = tmpl.target(args)
        if ident:
            return ident
    prefix = (ns.get("prefix") or "").strip()
    if prefix:
        log.warning(
            "scaffold: no source declared a target — falling back to --prefix=%r. "
            "If this scaffold opens PRs, set the source's identity flags "
            "(--owner/--repo for GitHub, --bitbucket-workspace/--bitbucket-repo for Bitbucket).",
            prefix,
        )
        return prefix
    raise ConfigError("scaffold: cannot derive agent target — pass --prefix or set the selected source's identity flags")


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Top-level flags every scaffold template shares.

    Was `ScaffoldArgs.add_common` before Phase 12 demoted the
    static-only namespace class to module functions."""
    parser.add_argument("--prefix", required=True, help="prefix prepended to every resource name")
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        choices=sorted(SOURCE_TEMPLATES.keys()),
        help="Source kind(s) to gather context from. Repeat for multiple.",
    )
    parser.add_argument(
        "--archetype",
        default="engineer",
        choices=sorted(ARCHETYPES.keys()),
        help="Agent role + tool filter (default: engineer)",
    )
    parser.add_argument(
        "--shape",
        default="plan-approve-act",
        choices=sorted(WORKFLOW_SHAPES.keys()),
        help="Workflow graph shape (default: plan-approve-act)",
    )
    parser.add_argument(
        "--trigger-kind",
        default="github_webhook",
        choices=sorted(TRIGGER_TEMPLATES.keys()),
        help="What kind of trigger creates tasks for this workflow",
    )
    parser.add_argument(
        "--llm-provider-key",
        default="anthropic",
        help="LLMProvider config key",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="LLM model id passed to LiteLLM as `<provider>/<model>`",
    )
    parser.add_argument(
        "--auth-mode",
        default="oauth",
        choices=["oauth", "pat"],
        help="GitHub auth mode (oauth handshake or stored PAT)",
    )
    parser.add_argument(
        "--github-secret-id",
        help="Secret UUID holding a GitHub PAT (with --auth-mode pat)",
    )
    parser.add_argument(
        "--company",
        default="",
        help=(
            "Company name whose extracted knowledge to splice into the "
            "agent's system_prompt. When omitted, the scaffold emits a "
            "knowledge-aware persona but without any cached sections."
        ),
    )
    parser.add_argument(
        "--knowledge-store",
        default="",
        help=("KnowledgeStore backend to read the splice from " "(default: postgres if BRIAR_DATABASE_URL is set, else file)"),
    )
    # Shared issue-author/assignee filters. Apply to every selected source
    # (github / bitbucket / jira); a per-source override (e.g.
    # --jira-authors-allow, see the source-specific help) wins when given.
    group = parser.add_argument_group("context filters", "Apply to all --source kinds; per-source overrides win.")
    for field, blurb in _FILTER_FIELDS:
        group.add_argument(f"--{field.replace('_', '-')}", action="append", default=[], help=blurb)


# Canonical filter fields shared across every source kind, plus the
# help blurb shown once on the shared flag.
_FILTER_FIELDS = (
    ("authors_allow", "only include issues whose author is in this list (repeatable)"),
    ("authors_block", "exclude issues whose author is in this list (repeatable)"),
    ("assignees_allow", "only include issues with an assignee in this list (repeatable)"),
    ("assignees_block", "exclude issues with an assignee in this list (repeatable)"),
)
_FILTER_SUFFIXES = tuple(f"_{field}" for field, _ in _FILTER_FIELDS)


def attach_source_arguments(parser: argparse.ArgumentParser) -> None:
    # One labelled group per source so `-h` reads as navigable sections
    # (github options / jira options / …) instead of ~30 flat flags.
    for name, tmpl in SOURCE_TEMPLATES.items():
        tmpl.add_arguments(parser.add_argument_group(f"{name} source options"))
    # The per-source filter flags (--jira-authors-allow, …) are now covered
    # by the shared --authors-allow/-block / --assignees-allow/-block flags.
    # Keep them registered (back-compat + per-source override) but hide them
    # from -h so the scaffold help shows one filter set, not three.
    for action in parser._actions:
        if action.dest.endswith(_FILTER_SUFFIXES) and action.help is not argparse.SUPPRESS:
            action.help = argparse.SUPPRESS


def attach_trigger_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("trigger options")
    for tmpl in TRIGGER_TEMPLATES.values():
        tmpl.add_arguments(group)


# Back-compat alias for the composer entry point.
compose_bundle = ScaffoldComposer.compose
