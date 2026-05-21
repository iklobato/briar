"""Task-scoped: fetch full body, ACs, and comments for ONE ticket.

Invoked by `briar agent` when the operator passes a specific ticket
key. Output is spliced into that single agent run's system prompt —
it does NOT go into the per-company knowledge blob.

The agent uses this section to anchor its plan against the actual
ticket description rather than just the title + assignee summary
that the scheduled `active-tickets` extractor surfaces."""

from __future__ import annotations

import argparse
import logging
from typing import List

from briar.extract._tracker import Comment
from briar.extract.base import EMPTY_SECTION, ExtractedSection, TaskScopedTrackerExtractor


log = logging.getLogger(__name__)


class FetchTicketContext(TaskScopedTrackerExtractor):
    name = "ticket-context"
    description = "Full body + ACs + comments for ONE specific ticket"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--ticket-project",
            required=True,
            help="Tracker project key (Jira: PROJ; Linear team: ENG; "
            "GH Issues: owner/repo; BB Issues: workspace/repo)",
        )
        parser.add_argument(
            "--ticket-key",
            required=True,
            help="Ticket identifier (Jira: PROJ-123; GH/BB: #42; Linear: ENG-7)",
        )

    def fetch(self, args: argparse.Namespace) -> ExtractedSection:
        tracker = self._tracker(args)
        ticket = tracker.get_ticket(args.ticket_project, args.ticket_key)
        if not ticket.title and not ticket.description:
            log.warning("ticket-context: %s not found or empty", args.ticket_key)
            return EMPTY_SECTION

        comments: List[Comment] = tracker.list_comments(args.ticket_project, args.ticket_key)
        transitions = tracker.list_status_transitions(args.ticket_project, args.ticket_key)

        body_parts: List[str] = [
            f"**Key**: {ticket.key}",
            f"**Status**: {ticket.status}",
        ]
        if ticket.kind:
            body_parts.append(f"**Type**: {ticket.kind}")
        if ticket.priority:
            body_parts.append(f"**Priority**: {ticket.priority}")
        body_parts.append(f"**Reporter**: {ticket.reporter or '(unset)'}")
        body_parts.append(f"**Assignee**: {ticket.assignee or '(unset)'}")
        if ticket.labels:
            body_parts.append(f"**Labels**: {', '.join(ticket.labels)}")
        if transitions:
            body_parts.append(f"**Status history**: {' → '.join(transitions)}")
        body_parts.append("")
        body_parts.append("### Description")
        body_parts.append("")
        body_parts.append(ticket.description or "_(no description)_")
        if comments:
            body_parts.append("")
            body_parts.append(f"### Comments ({len(comments)})")
            body_parts.append("")
            for c in comments[:20]:
                body_parts.append(f"**{c.author}** ({c.created_at}):")
                body_parts.append(c.body)
                body_parts.append("")
            if len(comments) > 20:
                body_parts.append(f"_…and {len(comments) - 20} more (older); fetch with --max-comments to see all_")

        return ExtractedSection(
            title=f"Ticket context — {ticket.key}: {ticket.title}",
            body="\n".join(body_parts),
            data={
                "key": ticket.key,
                "title": ticket.title,
                "status": ticket.status,
                "labels": ticket.labels,
                "comment_count": len(comments),
            },
        )
