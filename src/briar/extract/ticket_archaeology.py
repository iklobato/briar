"""Mine the closed-ticket history of one or more projects.

Symmetric to `pr-archaeology`. Surfaces: median time-to-close, top
reporters/assignees, label distribution. Helps agents match the
project's established triage cadence + categorisation."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from statistics import median
from typing import Any, Dict, List

from briar.extract._tracker import Ticket
from briar.extract._time_util import UNPARSABLE_HOURS, hours_between
from briar.extract.base import ExtractedSection, TrackerBackedExtractor, empty_section


class ExtractTicketArchaeology(TrackerBackedExtractor):
    UNPARSABLE_HOURS = UNPARSABLE_HOURS
    _hours_between = staticmethod(hours_between)

    name = "ticket-archaeology"
    heading = "Ticket archaeology"
    description = "closed-ticket patterns, assignee + label cadence"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--ticket-archaeology-project",
            action="append",
            default=[],
            help="Tracker project key to mine. Repeatable.",
        )
        parser.add_argument(
            "--ticket-max",
            type=int,
            default=100,
            help="Max closed tickets per project (default: 100)",
        )

    def is_available(self, args: argparse.Namespace) -> bool:
        if not args.ticket_archaeology_project:
            return False
        try:
            tracker = self._tracker(args)
        except Exception:  # noqa: BLE001
            return False
        return tracker.is_available()

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        tracker = self._tracker(args)
        per_project: List[ExtractedSection] = []
        for project in args.ticket_archaeology_project:
            section = self._mine_project(project, args.ticket_max, tracker)
            if not section.is_empty:
                per_project.append(section)
        if not per_project:
            return empty_section()
        return ExtractedSection(
            title=f"Ticket archaeology — {len(per_project)} project(s)",
            body=("Patterns from the most recent closed tickets. Agents " "should match the project's triage + labelling conventions."),
            subsections=per_project,
        )

    def _mine_project(self, project: str, max_tickets: int, tracker) -> ExtractedSection:
        tickets: List[Ticket] = tracker.list_tickets(project, state="closed", max_count=max_tickets)
        if not tickets:
            return empty_section()

        cycle_hours = [h for h in (self._hours_between(t.created_at, t.updated_at) for t in tickets) if h >= 0]
        reporters: Counter = Counter()
        assignees: Counter = Counter()
        labels: Counter = Counter()
        kinds: Counter = Counter()
        for t in tickets:
            reporters[t.reporter or "?"] += 1
            assignees[t.assignee or "?"] += 1
            kinds[t.kind or "?"] += 1
            for lbl in t.labels:
                labels[lbl] += 1

        data: Dict[str, Any] = {
            "project": project,
            "closed_ticket_count": len(tickets),
            "median_close_hours": (round(median(cycle_hours), 2) if cycle_hours else None),
            "top_reporters": reporters.most_common(5),
            "top_assignees": assignees.most_common(5),
            "top_labels": labels.most_common(5),
            "kinds": kinds.most_common(5),
        }
        body_lines = [f"- closed ticket sample: **{data['closed_ticket_count']}**"]
        if data["median_close_hours"] is not None:
            body_lines.append(f"- median time-to-close: **{data['median_close_hours']}h**")
        for label, items in (
            ("top reporters", data["top_reporters"]),
            ("top assignees", data["top_assignees"]),
            ("top labels", data["top_labels"]),
            ("kinds", data["kinds"]),
        ):
            if items:
                joined = ", ".join(f"{u}({n})" for u, n in items)
                body_lines.append(f"- {label}: {joined}")
        return ExtractedSection(
            title=project,
            body="\n".join(body_lines),
            data=data,
        )
