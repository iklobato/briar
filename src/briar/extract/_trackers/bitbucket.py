"""Bitbucket Issues `TrackerProvider`.

Bitbucket Cloud has its own issue tracker per repo. The Cloud client
in ``atlassian-python-api`` exposes it via ``repo.issues``."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from briar.auth.logins import BitbucketLogin, CompanyLogins
from briar.decorators import swallow_errors
from briar.extract._tracker import Comment, Ticket, TrackerProvider


log = logging.getLogger(__name__)


class BitbucketIssuesTracker(TrackerProvider):
    kind = "bitbucket-issues"
    project_is_repo_slug = True
    BASE = "https://api.bitbucket.org/"

    def __init__(self, *, logins: Optional[CompanyLogins] = None) -> None:
        self._logins = logins
        self._client = None

    def _login(self) -> BitbucketLogin:
        return (self._logins or CompanyLogins.defaults("")).bitbucket()

    def _cloud(self):
        if self._client is None:
            from atlassian.bitbucket.cloud import Cloud

            # timeout=30: same as the messaging-side Cloud client (Phase 2);
            # extract-side tracker was missed until the re-audit.
            self._client = Cloud(url=self.BASE, timeout=30, **self._login().client_kwargs())
        return self._client

    def _resolve_addr(self, project: str) -> Tuple[str, str]:
        if "/" in project:
            ws, _, slug = project.partition("/")
            return ws, slug
        return self._login().workspace(), project

    def is_available(self) -> bool:
        return self._login().ready()

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
        return replace(ticket, description=str(content)[:8000])

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
