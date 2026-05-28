"""Workflow shape registry.

The same set of agents + tools can be composed into very different
runtime behaviours by varying the graph shape. Three shipped shapes
+ a frozen dataclass that holds each one:

  plan-approve-act   plan → human_checkpoint → implement | comment
                     (the standard "agent proposes, human approves,
                      agent implements" loop)
  one-shot           agent (no checkpoint, no branching)
                     (best for "fix all open review comments" jobs —
                      the cron trigger fires hourly, the agent just
                      does the work)
  triage             agent (read-only — drops the implement tools off
                            the bound list at scaffold time)

The ABC + per-shape-file structure that lived here previously cost 4
files for 3 one-method classes. A dataclass with a `build_graph`
callable captures the same variation in 1 file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

from briar._registry import build_registry


# ─── Dataclass + helpers ──────────────────────────────────────────────


@dataclass(frozen=True)
class WorkflowShape:
    """Strategy via data, not via subclass. `build_graph(agent_key)`
    returns the orchestrator-consumed graph dict."""

    name: str
    description: str
    build_graph: Callable[[str], Dict[str, Any]]


# ─── Prompts ──────────────────────────────────────────────────────────


_RUN_PROMPT = (
    "Execute your archetype's directive in ONE pass. Procedure:\n"
    "\n"
    "1. **Read first**. Walk the consumed knowledge sections listed in "
    "your archetype, in the order they're declared. Stop reading only "
    "after you've extracted the per-section signal the archetype "
    "requires.\n"
    "2. **Act**. Use ONLY the tools bound to your archetype. Each tool "
    "call should be justified by something you read in step 1; if you "
    "can't cite a section, don't make the call.\n"
    "3. **Summarise**. End your output with a `## Actions` block "
    "listing every tool you invoked, each line: "
    "`<tool> — <one-line rationale citing a section>`. If you took no "
    "actions because the knowledge said nothing needed doing, say so "
    "and explain.\n"
    "\n"
    "Do not act on things outside your archetype's scope. If you notice "
    "an issue your archetype isn't allowed to fix, mention it in the "
    "summary so a human can decide."
)

_TRIAGE_PROMPT = (
    "Triage each item from the gathered sources. Procedure per item:\n"
    "\n"
    "1. **Read** the consumed knowledge sections in your archetype's "
    "order. You will use `codebase-conventions` for the area label, "
    "`github-deployments` for severity (look for live incidents), "
    "`pr-archaeology` for the reviewer hint, and `active-work` for "
    "duplicate-PR detection.\n"
    "2. **Label**. Apply ONE area label (derived from the modules the "
    "item references) and ONE severity label (`crit` / `bug` / "
    "`enhancement`). Use `add_labels`. Do not invent labels that "
    "aren't already justifiable from the knowledge.\n"
    "3. **Comment**. Post ONE comment per item via "
    "`comment_on_issue` containing: (a) one-sentence restatement of "
    "the ticket, (b) labels applied + the section that justified each, "
    "(c) likely reviewer from `pr-archaeology`, (d) a duplicate signal "
    "(link to an open PR) if `active-work` shows one.\n"
    "4. **Stop**. Do not commit, do not open PRs, do not transition "
    "status. Triager's output is labels + comment, nothing more.\n"
    "\n"
    "End with a `## Triaged` block listing each item handled, one line "
    "per item: `<item-ref> — <labels-applied>`."
)

_PLAN_PROMPT = (
    "Produce the implementation plan for this ticket. Follow your "
    "archetype's reading order strictly — do not skip a section to save "
    "tokens. Output the plan in this exact shape:\n"
    "\n"
    "## Reading\n"
    "Per consumed extractor section, write ONE bullet stating what you "
    "learned and how it constrains the change. Cite the section name "
    "(e.g. `codebase-conventions`) at the start of each bullet.\n"
    "\n"
    "## Scope\n"
    "Decompose the ticket into discrete asks before planning. For each "
    "ask, write ONE line in this shape:\n"
    "    [IN]  <ask> — <why it's in scope>\n"
    "    [OUT] <ask> — <why it's out of scope: too large / unrelated /\n"
    "                  needs a separate ticket / lacks input / etc.>\n"
    "Rules for the split:\n"
    "1. Default-in: most tickets are a single ask. Only split when the "
    "ticket text genuinely contains multiple distinct requests.\n"
    "2. Default-out when: the ask would touch a system the ticket "
    "didn't name; the ask would require >5 files or >300 LOC of net new "
    "code; the ask lacks a verifiable acceptance criterion in the ticket; "
    "the ask is a refactor without a paired bug fix.\n"
    "3. The plan below addresses ONLY the `[IN]` asks. Out-of-scope asks "
    "go into Risks (see below) as future-ticket suggestions, not as work "
    "you'll do in this PR.\n"
    "4. If EVERY ask is `[OUT]`, stop here: write a one-paragraph "
    "explanation under Scope and let the human reject the plan. The "
    "rejection-comment path will then post your explanation back to the "
    "issue.\n"
    "\n"
    "## Files to change\n"
    "Bulleted list, addressing ONLY the `[IN]` asks above. Each line: "
    "`<path> — <one-line why>`. Cross-check each path against the "
    "`active-work` section; if a path appears in an open PR, REMOVE it "
    "and adjust the plan. If the list exceeds 5 files OR you anticipate "
    ">300 LOC, flag the entire ticket as out-of-scope (move ASKs to OUT "
    "and stop).\n"
    "\n"
    "## Diff sketch\n"
    "Compact diff-style block: `+ added` / `- removed` / `~ modified`. "
    "Reviewers should be able to mentally apply it in one read.\n"
    "\n"
    "## Tests to run\n"
    "The exact commands the reviewer will run to verify (from "
    "`codebase-conventions`). If there are no tests, propose adding one "
    "and justify the test framework against what the project already uses.\n"
    "\n"
    "## PR title + body\n"
    "Title ≤72 chars. Body: what / why / test plan / risks. The "
    "reviewer hint should be the highest-cadence reviewer for the area "
    "from `pr-archaeology`.\n"
    "\n"
    "## Risks\n"
    "Anything a reviewer should look at twice. List every `[OUT]` ask "
    "here as a suggested follow-up ticket (one line each: title + "
    "one-sentence rationale), then call out any other risks specific to "
    "the IN-scope work. Empty section ('none identified') is allowed — "
    "but only after you've genuinely checked and the Scope block is also "
    "fully `[IN]`.\n"
    "\n"
    "STOP after writing the plan. Do not call any mutating tools yet. A "
    "human will approve or reject before you implement."
)

_IMPLEMENT_PROMPT = (
    "The plan above was approved. Implement it now using your bound "
    "tools. Procedure:\n"
    "\n"
    "1. Branch off the default branch with `briar/issue-<N>` or "
    "`briar/<short-slug>` — never commit straight to main.\n"
    "2. Make the change with `github.commit_files`. One commit per "
    "logical step; subject ≤72 chars; body explains why when the change "
    "isn't self-evident. Match the linter and formatter named in the "
    "`codebase-conventions` section — if they're configured to run on "
    "commit, expect them to.\n"
    "3. When the diff is complete, open a draft PR with "
    "`github.open_pr`. Title + body VERBATIM from the approved plan. "
    "Mark as draft, not ready-for-review.\n"
    "4. End your output with the PR URL on its own line, no extra text.\n"
    "\n"
    "If any tool call fails: retry once, then surface the error verbatim "
    "and stop. Do not invent a fictitious PR URL or commit SHA."
)

_COMMENT_PROMPT = (
    "The plan was rejected. Post ONE comment on the originating issue "
    "via `github.comment_on_issue`. The comment must contain:\n"
    "\n"
    "1. A one-line restatement of the rejection reason.\n"
    "2. The specific next step the issue owner needs to take — a "
    "missing input, a clarification, a precondition. Do NOT say "
    "'please clarify' without naming what.\n"
    "3. A pointer to the rejected plan so the human can revisit if the "
    "rejection turns out to have been wrong.\n"
    "\n"
    "Do not open a PR. Do not commit code. End your output with the "
    "comment URL on its own line."
)


# ─── Graph builders (one function per shape) ──────────────────────────


def _one_shot_graph(agent_key: str) -> Dict[str, Any]:
    return {
        "process": "sequential",
        "entry": "run",
        "nodes": [
            {"id": "run", "kind": "agent", "agent_key": agent_key, "prompt": _RUN_PROMPT, "next": ""},
        ],
    }


def _triage_graph(agent_key: str) -> Dict[str, Any]:
    return {
        "process": "sequential",
        "entry": "triage",
        "nodes": [
            {"id": "triage", "kind": "agent", "agent_key": agent_key, "prompt": _TRIAGE_PROMPT, "next": ""},
        ],
    }


def _plan_approve_act_graph(agent_key: str) -> Dict[str, Any]:
    """Plan → human_checkpoint → branch → (implement | comment).

    The orchestrator routes via dedicated `branch` nodes — a
    `human_checkpoint` only writes its decision to context and follows
    its own `next`."""
    return {
        "process": "sequential",
        "entry": "plan",
        "nodes": [
            {"id": "plan", "kind": "agent", "agent_key": agent_key, "prompt": _PLAN_PROMPT, "next": "approve"},
            {"id": "approve", "kind": "human_checkpoint", "prompt": "Approve the plan before implementation begins.", "next": "choose"},
            {"id": "choose", "kind": "branch", "branches": {"approve": "implement", "reject": "comment"}},
            {"id": "implement", "kind": "agent", "agent_key": agent_key, "prompt": _IMPLEMENT_PROMPT, "next": ""},
            {"id": "comment", "kind": "agent", "agent_key": agent_key, "prompt": _COMMENT_PROMPT, "next": ""},
        ],
    }


# ─── Registry ─────────────────────────────────────────────────────────


WORKFLOW_SHAPES: Dict[str, WorkflowShape] = build_registry(
    (
        WorkflowShape("plan-approve-act", "agent plans → human approves → branch routes to act (or comment on reject)", _plan_approve_act_graph),
        WorkflowShape("one-shot", "single agent node, no checkpoint, no branching", _one_shot_graph),
        WorkflowShape("triage", "single agent that classifies + comments, no write actions", _triage_graph),
    ),
    kind="workflow shape",
)


__all__ = ["WorkflowShape", "WORKFLOW_SHAPES"]
