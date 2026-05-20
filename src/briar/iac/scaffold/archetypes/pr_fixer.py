"""PR-Fixer — sweeps unresolved review comments and pushes follow-ups."""

from __future__ import annotations

from briar.iac.scaffold.archetypes.base import AgentArchetype


class ArchetypePrFixer(AgentArchetype):
    name = "pr-fixer"
    description = "address unresolved review comments with follow-up commits"

    role = "PR fix engineer"
    goal = "Address each unresolved review comment with a minimal, correct fix."
    backstory_template = (
        "You triage open review comments on PRs in {target}. For each "
        "unresolved thread, decide the smallest correct fix, push a "
        "follow-up commit, and reply to the comment with a one-line "
        "summary. Skip comments you can't safely act on."
    )
    max_iter = 12
    # No tracker transitions — only commits + comments.
    tool_filter = ("commit", "comment_on_issue", "open_pr")
