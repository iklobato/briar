"""Knowledge extractor registry — Strategy + Factory.

Folder conveys the verb (`extract/`); files use bare kind names; class
names follow `Extract<Kind>` so a single grep turns up every
implementation."""

from __future__ import annotations

from typing import Dict

from briar._registry import build_registry
from briar.extract.active_tickets import ExtractActiveTickets
from briar.extract.active_work import ExtractActiveWork
from briar.extract.aws_infra import ExtractAwsInfra
from briar.extract.base import ExtractedSection, KnowledgeExtractor, TaskScopedExtractor
from briar.extract.code_hotspots import ExtractCodeHotspots
from briar.extract.codebase_conventions import ExtractCodebaseConventions
from briar.extract.github_deployments import ExtractGithubDeployments
from briar.extract.pr_archaeology import ExtractPrArchaeology
from briar.extract.pr_review_context import FetchPrReviewContext
from briar.extract.reviewer_profile import ExtractReviewerProfile
from briar.extract.ticket_archaeology import ExtractTicketArchaeology
from briar.extract.ticket_context import FetchTicketContext


EXTRACTORS: Dict[str, KnowledgeExtractor] = build_registry(
    (
        ExtractPrArchaeology(),
        ExtractAwsInfra(),
        ExtractActiveWork(),
        ExtractGithubDeployments(),
        ExtractCodebaseConventions(),
        ExtractActiveTickets(),
        ExtractTicketArchaeology(),
        ExtractReviewerProfile(),
        ExtractCodeHotspots(),
    ),
    kind="knowledge extractor",
)


# Task-scoped extractors live in a SEPARATE registry. They're not
# invoked by the runbook executor (no schedule); the agent runner
# fetches them at agent-invocation time when the operator passes
# --ticket-key / --pr-target-number.
TASK_SCOPED_EXTRACTORS: Dict[str, TaskScopedExtractor] = build_registry(
    (FetchTicketContext(), FetchPrReviewContext()),
    kind="task-scoped extractor",
)


__all__ = ["EXTRACTORS", "KnowledgeExtractor", "ExtractedSection"]
