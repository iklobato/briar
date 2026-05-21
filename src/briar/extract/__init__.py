"""Knowledge extractor registry — Strategy + Factory.

Folder conveys the verb (`extract/`); files use bare kind names; class
names follow `Extract<Kind>` so a single grep turns up every
implementation."""

from __future__ import annotations

from typing import Dict

from briar.extract.active_tickets import ExtractActiveTickets
from briar.extract.active_work import ExtractActiveWork
from briar.extract.aws_infra import ExtractAwsInfra
from briar.extract.base import ExtractedSection, KnowledgeExtractor
from briar.extract.codebase_conventions import ExtractCodebaseConventions
from briar.extract.github_deployments import ExtractGithubDeployments
from briar.extract.pr_archaeology import ExtractPrArchaeology
from briar.extract.ticket_archaeology import ExtractTicketArchaeology


EXTRACTORS: Dict[str, KnowledgeExtractor] = {
    e.name: e
    for e in (
        ExtractPrArchaeology(),
        ExtractAwsInfra(),
        ExtractActiveWork(),
        ExtractGithubDeployments(),
        ExtractCodebaseConventions(),
        ExtractActiveTickets(),
        ExtractTicketArchaeology(),
    )
}


__all__ = ["EXTRACTORS", "KnowledgeExtractor", "ExtractedSection"]
