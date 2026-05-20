"""Triager — read-only classification + labelling + commenting."""

from __future__ import annotations

from briar.iac.scaffold.archetypes.base import AgentArchetype


class ArchetypeTriager(AgentArchetype):
    name = "triager"
    description = "classify and label tickets; comment with a short summary"

    role = "Triage analyst"
    goal = "Read incoming tickets, classify them by area/severity, add the " "appropriate labels, and post a single-paragraph summary comment."
    backstory_template = (
        "You triage tickets in {target}. You never commit code or open "
        "PRs — your output is the categorisation itself, expressed as "
        "labels + a comment. Read cloud-source context if available for "
        "deduplication signals."
    )
    max_iter = 5
    # Comments + labels only, no commits / PR opens / transitions.
    tool_filter = ("comment_on_issue", "add_labels", "comment")
