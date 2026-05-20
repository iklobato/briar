"""Reconciler registry. Apply order is dependency-first; destroy walks
the reverse."""

from __future__ import annotations

from typing import List

from briar.iac.reconcilers.agent import ReconcileAgent
from briar.iac.reconcilers.base import ResourceReconciler
from briar.iac.reconcilers.llm_model import ReconcileLlmModel
from briar.iac.reconcilers.llm_provider import ReconcileLlmProvider
from briar.iac.reconcilers.source import ReconcileSource
from briar.iac.reconcilers.tool import ReconcileTool
from briar.iac.reconcilers.trigger import ReconcileTrigger
from briar.iac.reconcilers.workflow import ReconcileWorkflow


# Single source of truth for ordering. Adding a new resource = one
# subclass of `ResourceReconciler` + one entry here.
RECONCILER_ORDER: List[ResourceReconciler] = [
    ReconcileLlmProvider(),
    ReconcileLlmModel(),
    ReconcileSource(),
    ReconcileTool(),
    ReconcileAgent(),
    ReconcileWorkflow(),
    ReconcileTrigger(),
]

__all__ = ["ResourceReconciler", "RECONCILER_ORDER"]
