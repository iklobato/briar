"""Plan → human_checkpoint → branch → (implement | comment).

The orchestrator routes via dedicated `branch` nodes — a
`human_checkpoint` only writes its decision to context and follows its
own `next`. So the shape needs:
    plan       → approve
    approve    → choose          (human_checkpoint, next=choose)
    choose     → implement | comment  (branch reads _last_decision)
"""

from __future__ import annotations

from typing import Any, Dict

from briar.iac.scaffold.shapes.base import WorkflowShape


class ShapePlanApproveAct(WorkflowShape):
    name = "plan-approve-act"
    description = (
        "agent plans → human approves → branch routes to "
        "act (or comment on reject)"
    )

    def build_graph(self, agent_key: str) -> Dict[str, Any]:
        return {
            "process": "sequential",
            "entry": "plan",
            "nodes": [
                {
                    "id": "plan", "kind": "agent",
                    "agent_key": agent_key,
                    "prompt": (
                        "Read the gathered context and produce a concrete "
                        "plan with: (1) the exact files to change, "
                        "(2) the specific diff, (3) the PR title + body."
                    ),
                    "next": "approve",
                },
                {
                    "id": "approve", "kind": "human_checkpoint",
                    "prompt": "Approve the plan before implementation begins.",
                    "next": "choose",
                },
                {
                    "id": "choose", "kind": "branch",
                    "branches": {
                        "approve": "implement",
                        "reject": "comment",
                    },
                },
                {
                    "id": "implement", "kind": "agent",
                    "agent_key": agent_key,
                    "prompt": (
                        "The plan was approved. Implement it now using "
                        "your bound tools. Commit files with "
                        "`github.commit_files` (branch off main, "
                        "self-named like `briar/issue-N`), then open a "
                        "draft pull request via `github.open_pr`. End "
                        "with the PR URL on its own line."
                    ),
                    "next": None,
                },
                {
                    "id": "comment", "kind": "agent",
                    "agent_key": agent_key,
                    "prompt": (
                        "The plan was rejected. Post a single comment on "
                        "the originating issue via "
                        "`github.comment_on_issue` explaining why no PR "
                        "will be opened."
                    ),
                    "next": None,
                },
            ],
        }
