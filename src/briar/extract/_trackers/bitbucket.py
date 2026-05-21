"""Bitbucket Issues `TrackerProvider`.

Bitbucket Cloud has its own issue tracker per repo. The Cloud client
in ``atlassian-python-api`` exposes it via ``repo.issues``."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.extract._tracker import Comment, Ticket, TrackerProvider


log = logging.getLogger(__name__)


class BitbucketIssuesTracker(TrackerProvider):
    kind = "bitbucket-issues"
    BASE = "https://api.bitbucket.org/"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._username = CredEnv.BITBUCKET_USERNAME.read(company=company) if company else ""
        self._app_password = CredEnv.BITBUCKET_APP_PASSWORD.read(company=company) if company else ""
        self._workspace_slug = CredEnv.BITBUCKET_WORKSPACE.read(company=company) if company else ""
        self._client = None

    def _cloud(self):
        if self._client is None:
            from atlassian.bitbucket.cloud import Cloud

            self._client = Cloud(url=self.BASE, username=self._username, password=self._app_password)
        return self._client

    def _resolve_addr(self, project: str) -> Tuple[str, str]:
        if "/" in project:
            ws, _, slug = project.partition("/")
            return ws, slug
        return self._workspace_slug, project

    def is_available(self) -> bool:
        return bool(self._username and self._app_password and self._workspace_slug)

    @swallow_errors(default=[], message="bitbucket-issues list_tickets")
    def list_tickets(self, project: str, *, state: str, max_count: int) -> List[Ticket]:
        ws, slug = self._resolve_addr(project)
        # Bitbucket issue states: new | open | resolved | on hold | invalid |
        # duplicate | wontfix | closed. Map "open" → new+open+on hold;
        # "closed" → resolved+closed+wontfix+duplicate+invalid.
        if state == "closed":
            q = '(state="resolved" OR state="closed" OR state="wontfix" OR state="duplicate" OR state="invalid")'
        else:
            q = '(state="new" OR state="open" OR state="on hold")'
        repo = self._cloud().workspaces.get(ws).repositories.get(slug)
        out: List[Ticket] = []
        # Bitbucket issues are accessed via the raw API; the cloud client
        # exposes a `.get("issues", ...)` shortcut.
        envelope = repo.get("issues", params={"q": q, "sort": "-updated_on", "pagelen": min(max_count, 100)})
        values = (envelope or {}).get("values", []) if isinstance(envelope, dict) else []
        for issue in values[:max_count]:
            out.append(self._to_ticket(issue, project))
        return out

    @swallow_errors(default=[], message="bitbucket-issues list_comments")
    def list_comments(self, project: str, ticket_key: str) -> List[Comment]:
        ws, slug = self._resolve_addr(project)
        issue_id = ticket_key.lstrip("#")
        repo = self._cloud().workspaces.get(ws).repositories.get(slug)
        envelope = repo.get(f"issues/{issue_id}/comments", params={"pagelen": 50})
        rows = (envelope or {}).get("values", []) if isinstance(envelope, dict) else []
        out: List[Comment] = []
        for c in rows:
            author = (c.get("user") or {}).get("display_name") or ""
            content = (c.get("content") or {}).get("raw") or ""
            out.append(Comment(author=author, body=content[:500], created_at=c.get("created_on") or ""))
        return out

    @swallow_errors(default=None, message="bitbucket-issues get_ticket")
    def get_ticket(self, project: str, ticket_key: str) -> Ticket:
        ws, slug = self._resolve_addr(project)
        issue_id = ticket_key.lstrip("#")
        repo = self._cloud().workspaces.get(ws).repositories.get(slug)
        data = repo.get(f"issues/{issue_id}")
        if not isinstance(data, dict):
            return super().get_ticket(project, ticket_key)
        ticket = self._to_ticket(data, project)
        content = (data.get("content") or {}).get("raw") or ""
        return Ticket(
            key=ticket.key,
            title=ticket.title,
            reporter=ticket.reporter,
            assignee=ticket.assignee,
            status=ticket.status,
            kind=ticket.kind,
            priority=ticket.priority,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            labels=ticket.labels,
            url=ticket.url,
            description=str(content)[:8000],
        )

    @staticmethod
    def _to_ticket(issue: Dict[str, Any], project: str) -> Ticket:
        reporter = (issue.get("reporter") or {}).get("display_name") or ""
        assignee = (issue.get("assignee") or {}).get("display_name") or ""
        return Ticket(
            key=f"#{issue.get('id')}",
            title=str(issue.get("title") or "")[:200],
            reporter=reporter,
            assignee=assignee,
            status=str(issue.get("state") or ""),
            kind=str(issue.get("kind") or ""),
            priority=str(issue.get("priority") or ""),
            created_at=str(issue.get("created_on") or ""),
            updated_at=str(issue.get("updated_on") or ""),
            labels=[],  # Bitbucket Cloud issues don't have labels in the same sense
            url=str((issue.get("links") or {}).get("html", {}).get("href") or ""),
        )
