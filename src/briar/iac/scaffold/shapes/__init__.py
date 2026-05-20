"""Workflow shape registry — Strategy + Builder for `workflow.graph`.

The same set of agents + tools can be composed into very different
runtime behaviours by varying the graph shape. The three shipped
shapes:

    plan-approve-act   plan → human_checkpoint → implement | comment
                       (the standard "agent proposes, human approves,
                        agent implements" loop)

    one-shot           agent (no checkpoint, no branching)
                       (best for "fix all open review comments" jobs —
                        the cron trigger fires hourly, the agent just
                        does the work)

    triage             agent (read-only — drops the implement tools off
                              the bound list at scaffold time)

Adding a new shape = one subclass + one registry entry."""

from __future__ import annotations

from typing import Any, ClassVar, Dict

from briar.iac.scaffold.shapes.base import WorkflowShape
from briar.iac.scaffold.shapes.one_shot import ShapeOneShot
from briar.iac.scaffold.shapes.plan_approve_act import ShapePlanApproveAct
from briar.iac.scaffold.shapes.triage import ShapeTriage


WORKFLOW_SHAPES: Dict[str, WorkflowShape] = {
    s.name: s for s in (
        ShapePlanApproveAct(),
        ShapeOneShot(),
        ShapeTriage(),
    )
}


__all__ = ["WorkflowShape", "WORKFLOW_SHAPES"]
