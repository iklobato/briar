"""Compose a config bundle from the four registries.

Shared by every concrete scaffold template (implementation, pr_fixes,
…) so adding a new top-level template only writes the small "which
defaults" delta — sources / archetype / shape / trigger come from the
shared registries."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from briar.iac.scaffold.archetypes import ARCHETYPES, AgentArchetype
from briar.iac.scaffold.shapes import WORKFLOW_SHAPES, WorkflowShape
from briar.iac.scaffold.sources import SOURCE_TEMPLATES, SourceTemplate
from briar.iac.scaffold.triggers import TRIGGER_TEMPLATES, TriggerTemplate


def _resolved_source_templates(kinds: List[str]) -> List[SourceTemplate]:
    out: List[SourceTemplate] = []
    for kind in kinds:
        tmpl = SOURCE_TEMPLATES.get(kind)
        if tmpl is None:
            known = ", ".join(sorted(SOURCE_TEMPLATES))
            raise SystemExit(f"unknown source kind {kind!r}; known: {known}")
        out.append(tmpl)
    return out


def _resolved(
    name: str,
    registry: Dict[str, Any],
    label: str,
) -> Any:
    tmpl = registry.get(name)
    if tmpl is None:
        known = ", ".join(sorted(registry))
        raise SystemExit(f"unknown {label} {name!r}; known: {known}")
    return tmpl


def compose_bundle(
    args: argparse.Namespace,
    *,
    target: str,
) -> Dict[str, Any]:
    """`target` is the human-readable identifier (e.g. iklobato/lightapi)
    used in the agent backstory."""
    prefix = args.prefix

    source_templates = _resolved_source_templates(args.source)
    archetype: AgentArchetype = _resolved(args.archetype, ARCHETYPES, "archetype")
    shape: WorkflowShape = _resolved(args.shape, WORKFLOW_SHAPES, "shape")
    trigger_template: TriggerTemplate = _resolved(
        args.trigger_kind, TRIGGER_TEMPLATES, "trigger_kind"
    )

    sources_block: List[Dict[str, Any]] = []
    tools_block: List[Dict[str, Any]] = []
    for tmpl in source_templates:
        sources_block.append(tmpl.build_source(args, prefix))
        tools_block.extend(tmpl.build_tools(args, prefix))

    tools_block = archetype.filter_tools(tools_block)

    persona = archetype.build_persona(target)
    agent_key = f"{prefix}-{archetype.name}"
    agent: Dict[str, Any] = {
        "key": agent_key,
        "name": agent_key,
        "role": persona["role"],
        "goal": persona["goal"],
        "backstory": persona["backstory"],
        "llm_model_key": f"{prefix}-model",
        "tool_keys": [t["key"] for t in tools_block],
        "source_keys": [s["key"] for s in sources_block],
        "max_iter": archetype.max_iter,
    }

    workflow_graph = shape.build_graph(agent_key)
    # The orchestrator interpolates `task.context` into each prompt
    # via `prompt.format(**context)`. The shape's prompts are generic
    # ("read the gathered context") — without explicit `{source_*}`
    # placeholders the agent never sees the source data even though
    # it was gathered. Splice them in here.
    workflow_graph["nodes"] = [
        _append_source_context(node, [s["name"] for s in sources_block])
        for node in workflow_graph["nodes"]
    ]

    workflow: Dict[str, Any] = {
        "key": f"{prefix}-workflow",
        "name": f"{prefix}-workflow",
        "description": (
            f"{archetype.description} for {target} ({shape.name})"
        ),
        "graph": workflow_graph,
    }

    bundle: Dict[str, Any] = {
        "version": 1,
        "llm_models": [{
            "key": f"{prefix}-model",
            "name": args.model,
            "provider_key": args.llm_provider_key,
            "display_name": args.model,
            "default_params": {"temperature": 0.2},
        }],
        "sources": sources_block,
        "tools": tools_block,
        "agents": [agent],
        "workflows": [workflow],
    }

    trigger_dict = trigger_template.build_trigger(
        args, prefix, workflow_key=f"{prefix}-workflow",
    )
    if trigger_dict is not None:
        bundle["triggers"] = [trigger_dict]
    return bundle


def _append_source_context(node: Dict[str, Any], source_names: List[str]) -> Dict[str, Any]:
    """For agent nodes, append a `{source_<name>}` placeholder block to
    the prompt for every gathered source the agent is bound to. The
    orchestrator's `prompt.format(**context)` then substitutes the
    actual fetched payload at run time."""
    if node.get("kind") != "agent" or not source_names:
        return node
    if not source_names:
        return node
    placeholders = "\n".join(
        f"## Source `{name}`\n{{source_{name}}}"
        for name in source_names
    )
    enriched_prompt = (
        f"{node.get('prompt', '').rstrip()}\n\n"
        f"--- gathered sources ---\n\n{placeholders}\n"
    )
    out = dict(node)
    out["prompt"] = enriched_prompt
    return out


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Top-level flags every scaffold template shares."""
    parser.add_argument("--prefix", required=True,
                        help="prefix prepended to every resource name")
    parser.add_argument(
        "--source", action="append", default=[],
        choices=sorted(SOURCE_TEMPLATES.keys()),
        help="Source kind(s) to gather context from. Repeat for multiple.",
    )
    parser.add_argument(
        "--archetype", default="engineer",
        choices=sorted(ARCHETYPES.keys()),
        help="Agent role + tool filter (default: engineer)",
    )
    parser.add_argument(
        "--shape", default="plan-approve-act",
        choices=sorted(WORKFLOW_SHAPES.keys()),
        help="Workflow graph shape (default: plan-approve-act)",
    )
    parser.add_argument(
        "--trigger-kind", default="github_webhook",
        choices=sorted(TRIGGER_TEMPLATES.keys()),
        help="What kind of trigger creates tasks for this workflow",
    )
    parser.add_argument(
        "--llm-provider-key", default="anthropic",
        help="LLMProvider config key (name OR kind of an existing provider)",
    )
    parser.add_argument(
        "--model", default="claude-sonnet-4-6",
        help="LLM model id passed to LiteLLM as `<provider>/<model>`",
    )
    # GitHub-source-specific (shared with the github archetype's auth mode)
    parser.add_argument(
        "--auth-mode", default="oauth", choices=["oauth", "pat"],
        help="GitHub auth mode (oauth handshake or stored PAT)",
    )
    parser.add_argument(
        "--github-secret-id",
        help="Secret UUID holding a GitHub PAT (with --auth-mode pat)",
    )


def attach_source_arguments(parser: argparse.ArgumentParser) -> None:
    """Let each source template contribute its own flags."""
    for tmpl in SOURCE_TEMPLATES.values():
        tmpl.add_arguments(parser)


def attach_trigger_arguments(parser: argparse.ArgumentParser) -> None:
    for tmpl in TRIGGER_TEMPLATES.values():
        tmpl.add_arguments(parser)
