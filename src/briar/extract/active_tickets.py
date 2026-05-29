"""Open tickets across configured projects.

Symmetric to `active-work` (open PRs). Helps agents avoid duplicating
in-flight work and surfaces which tickets already have an assignee.

Tracker-agnostic: talks to a `TrackerProvider`, never to Jira /
GitHub / Linear directly. ``--tracker bitbucket-issues`` routes the
same logic onto a different vendor."""

from __future__ import annotations

import argparse
from typing import List

from briar.extract._tracker import Ticket
from briar.extract.base import ExtractedSection, TrackerBackedExtractor

_MAX_TICKETS_PER_PROJECT = 25


class ExtractActiveTickets(TrackerBackedExtractor):
    name = "active-tickets"
    heading = "Active tickets"
    description = "open tickets across the configured projects"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--ticket-project",
            action="append",
            default=[],
            help="Tracker project key to scan for active tickets. Repeatable.",
        )

    _availability_arg = "ticket_project"

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        tracker = self._tracker(args)
        sections = [self._scan_project(p, tracker) for p in args.ticket_project]
        return ExtractedSection(
            title=f"Active tickets — {len(sections)} project(s)",
            body=(
                "Live snapshot of open tickets. Agents should check this "
                "before opening a duplicate — match on title + reporter to "
                "avoid stepping on in-flight work."
            ),
            subsections=sections,
        )

    def _scan_project(self, project: str, tracker) -> ExtractedSection:
        tickets: List[Ticket] = tracker.list_tickets(project, state="open", max_count=_MAX_TICKETS_PER_PROJECT)
        rows = [
            {
                "key": t.key,
                "title": t.title[:80],
                "reporter": t.reporter,
                "assignee": t.assignee,
                "status": t.status,
                "labels": t.labels[:5],
            }
            for t in tickets
        ]
        lines = [f"- {r['key']} {r['title']!r:80}  status={r['status']}  by={r['reporter']}  to={r['assignee']}" for r in rows]
        return ExtractedSection(
            title=f"{project} — {len(rows)} open ticket(s)",
            body="\n".join(lines) if lines else "_no open tickets_",
            data={"open_tickets": rows},
        )
