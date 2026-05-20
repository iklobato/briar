"""Engineer — reads the tracker, plans a fix, commits + opens PR."""

from __future__ import annotations

from briar.iac.scaffold.archetypes.base import AgentArchetype


class ArchetypeEngineer(AgentArchetype):
    name = "engineer"
    description = "implement changes from tickets; commit and open PRs"

    role = "Engineering agent"
    goal = "Implement requested changes and open a draft pull request."
    backstory_template = (
        "You implement changes for {target}. You read tickets from the "
        "bound trackers, query bound cloud sources for state, then commit "
        "code and open a draft PR. Reject ambiguous requests with a "
        "concrete clarifying question."
    )
    max_iter = 8
    # Engineer gets every tool — comment, transition, commit, open-pr.
    tool_filter = ()
