"""GitHub Issues `TrackerProvider`.

Reuses the same `GithubApi` facade as the GitHub repo provider — no
extra dependency. GitHub Issues use ``state=open|closed`` natively;
labels and assignees map onto the `Ticket` shape directly. ``project``
here is ``<owner>/<repo>`` to match every other GH call site."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Dict, List

from briar.decorators import swallow_errors
from briar.extract._gh import GithubApi
from briar.extract._tracker import Comment, Ticket, TrackerProvider


log = logging.getLogger(__name__)


# GitHub doesn't have a native "kind" or "priority" — derive both from
# label conventions. Extracted from an inline `if low in (...): kind = low`
# chain so adding a new kind/priority signal is one tuple entry.
_KIND_LABELS = frozenset({"bug", "feature", "task", "story", "chore"})
_PRIORITY_PREFIXES = ("priority/", "p")


def _kind_priority_from_labels(labels: List[str]) -> tuple:
    """Inspect each label once; return (kind, priority). First match wins
    for priority (preserves the previous `priority = priority or lbl` shape)."""
    kind = ""
    priority = ""
    for lbl in labels:
        low = lbl.lower()
        if not kind and low in _KIND_LABELS:
            kind = low
        if not priority and any(low.startswith(p) for p in _PRIORITY_PREFIXES):
            priority = lbl
    return kind, priority


class GithubIssuesTracker(TrackerProvider):
    kind = "github-issues"

    def __init__(self, *, company: str = "") -> None:
        # GITHUB_TOKEN is workspace-wide; company is ignored.
        self._company = company

    def is_available(self) -> bool:
        return bool(GithubApi.auth_token())

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        return ["GITHUB_TOKEN"]

    @swallow_errors(default=[], message="github-issues list_tickets")
    def list_tickets(self, project: str, *, state: str, max_count: int) -> List[Ticket]:
        gh_state = "closed" if state == "closed" else "open"
        pages_needed = max(1, (max_count // 100) + 1)
        # GitHub's /issues endpoint returns BOTH issues and PRs; filter out PRs.
        rows = GithubApi.get_paginated(
            f"/repos/{project}/issues?state={gh_state}&sort=updated&direction=desc",
            max_pages=pages_needed,
        )
        out: List[Ticket] = []
        for row in rows:
            if row.get("pull_request"):
                continue  # PRs come through the issues endpoint too; skip them
            out.append(self._to_ticket(row, project))
            if len(out) >= max_count:
                break
        return out

    @swallow_errors(default=[], message="github-issues list_comments")
    def list_comments(self, project: str, ticket_key: str) -> List[Comment]:
        # ticket_key may be "42", "#42", or "<owner>/<repo>#42" (PlanCard.key form)
        number = ticket_key.split("#")[-1]
        rows = GithubApi.get_paginated(
            f"/repos/{project}/issues/{number}/comments",
            max_pages=2,
        )
        out: List[Comment] = []
        for c in rows:
            author = (c.get("user") or {}).get("login") or ""
            body = c.get("body") or ""
            out.append(Comment(author=author, body=body[:500], created_at=c.get("created_at") or ""))
        return out

    @swallow_errors(default=None, message="github-issues get_ticket")
    def get_ticket(self, project: str, ticket_key: str) -> Ticket:
        number = ticket_key.split("#")[-1]
        data = GithubApi.get_json(f"/repos/{project}/issues/{number}")
        if not isinstance(data, dict):
            return super().get_ticket(project, ticket_key)
        ticket = self._to_ticket(data, project)
        return replace(ticket, description=str(data.get("body") or "")[:8000])

    @swallow_errors(default=[], message="github-issues list_status_transitions")
    def list_status_transitions(self, project: str, ticket_key: str) -> List[str]:
        # GitHub Issues don't have rich status states; only open ↔ closed.
        # The "timeline" event API gives us the open/close history, which
        # is enough to surface "reopened twice" patterns.
        number = ticket_key.split("#")[-1]
        events = GithubApi.get_paginated(
            f"/repos/{project}/issues/{number}/events",
            max_pages=1,
        )
        out: List[str] = []
        for e in events:
            event = e.get("event") or ""
            if event in ("closed", "reopened"):
                out.append(event)
        return out

    @staticmethod
    def _to_ticket(issue: Dict[str, Any], project: str) -> Ticket:
        labels_raw = issue.get("labels") or []
        labels: List[str] = []
        for l in labels_raw:
            if isinstance(l, dict):
                name = l.get("name") or ""
                if name:
                    labels.append(name)
            elif isinstance(l, str):
                labels.append(l)
        # GitHub doesn't have a native "kind" or "priority" — derive from labels.
        kind, priority = _kind_priority_from_labels(labels)
        return Ticket(
            key=f"#{issue.get('number')}",
            title=str(issue.get("title") or "")[:200],
            reporter=(issue.get("user") or {}).get("login") or "",
            assignee=(issue.get("assignee") or {}).get("login") or "",
            status=str(issue.get("state") or ""),
            kind=kind,
            priority=priority,
            created_at=str(issue.get("created_at") or ""),
            updated_at=str(issue.get("updated_at") or ""),
            labels=labels,
            url=str(issue.get("html_url") or ""),
        )
