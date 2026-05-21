"""Jira `TrackerProvider`.

Backed by ``atlassian-python-api``'s `Jira` client (same library that
backs `BitbucketProvider`). Auth: per-company
``JIRA_<COMPANY>_URL`` + ``JIRA_<COMPANY>_EMAIL`` +
``JIRA_<COMPANY>_TOKEN`` (Atlassian API token, NOT the password)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.extract._tracker import Comment, Ticket, TrackerProvider


log = logging.getLogger(__name__)


class JiraTracker(TrackerProvider):
    kind = "jira"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._url = CredEnv.JIRA_URL.read(company=company) if company else ""
        self._email = CredEnv.JIRA_EMAIL.read(company=company) if company else ""
        self._token = CredEnv.JIRA_TOKEN.read(company=company) if company else ""
        self._client = None

    def _jira(self):
        if self._client is None:
            from atlassian import Jira

            self._client = Jira(url=self._url, username=self._email, password=self._token, cloud=True)
        return self._client

    def is_available(self) -> bool:
        return bool(self._url and self._email and self._token)

    @swallow_errors(default=[], message="jira list_tickets")
    def list_tickets(self, project: str, *, state: str, max_count: int) -> List[Ticket]:
        # Jira state vocabulary uses `statusCategory`: To Do | In Progress | Done.
        # Map "open" → not Done; "closed" → Done.
        if state == "closed":
            jql = f'project = "{project}" AND statusCategory = "Done" ORDER BY updated DESC'
        else:
            jql = f'project = "{project}" AND statusCategory != "Done" ORDER BY updated DESC'
        result = self._jira().jql(jql, limit=max_count)
        issues = (result or {}).get("issues", []) if isinstance(result, dict) else []
        out: List[Ticket] = []
        for issue in issues[:max_count]:
            out.append(self._to_ticket(issue))
        return out

    @swallow_errors(default=[], message="jira list_comments")
    def list_comments(self, project: str, ticket_key: str) -> List[Comment]:
        result = self._jira().issue(ticket_key, fields="comment")
        comments = ((result or {}).get("fields", {}) or {}).get("comment", {}) or {}
        rows = comments.get("comments", []) if isinstance(comments, dict) else []
        out: List[Comment] = []
        for c in rows:
            author = (c.get("author") or {}).get("displayName") or ""
            body = c.get("body") or ""
            # Atlassian Document Format → fall back to str() if not a string
            if not isinstance(body, str):
                body = str(body)
            out.append(Comment(author=author, body=body[:500], created_at=c.get("created") or ""))
        return out

    @swallow_errors(default=None, message="jira get_ticket")
    def get_ticket(self, project: str, ticket_key: str) -> Ticket:
        # Fetch the issue with description + acceptance-criteria custom
        # fields. Atlassian Document Format → plain text via the library's
        # rendered fields fallback.
        issue = self._jira().issue(ticket_key, fields="*all")
        if not isinstance(issue, dict):
            return super().get_ticket(project, ticket_key)
        ticket = self._to_ticket(issue)
        fields = issue.get("fields") or {}
        description = fields.get("description")
        # Atlassian Document Format is a dict-of-content blocks; fall
        # back to str() for now (Jira's `expand=renderedFields` gives
        # rendered HTML, less useful for an LLM than the raw ADF text).
        if isinstance(description, dict):
            description = self._adf_to_text(description)
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
            description=str(description or "")[:8000],
        )

    @classmethod
    def _adf_to_text(cls, doc: Dict[str, Any]) -> str:
        """Flatten Atlassian Document Format into plain text. Walks
        every node, concatenates `text` values, inserts newlines at
        paragraph boundaries. Good enough for an LLM prompt; not a
        full ADF renderer."""
        out: List[str] = []
        cls._adf_walk(doc, out)
        return "".join(out)

    @classmethod
    def _adf_walk(cls, node: Any, out: List[str]) -> None:
        if isinstance(node, dict):
            kind = node.get("type", "")
            if kind == "text":
                out.append(str(node.get("text", "")))
                return
            for child in node.get("content") or []:
                cls._adf_walk(child, out)
            if kind in ("paragraph", "heading", "bulletList", "orderedList", "listItem", "codeBlock"):
                out.append("\n")
        elif isinstance(node, list):
            for item in node:
                cls._adf_walk(item, out)

    @swallow_errors(default=[], message="jira list_status_transitions")
    def list_status_transitions(self, project: str, ticket_key: str) -> List[str]:
        result = self._jira().issue(ticket_key, expand="changelog")
        histories = ((result or {}).get("changelog", {}) or {}).get("histories", []) or []
        names: List[str] = []
        for h in histories:
            for item in h.get("items") or []:
                if item.get("field") == "status":
                    name = item.get("toString") or ""
                    if name:
                        names.append(name)
        return names

    @staticmethod
    def _to_ticket(issue: Dict[str, Any]) -> Ticket:
        fields = issue.get("fields") or {}
        reporter = (fields.get("reporter") or {}).get("displayName") or ""
        assignee = (fields.get("assignee") or {}).get("displayName") or ""
        status = (fields.get("status") or {}).get("name") or ""
        kind = (fields.get("issuetype") or {}).get("name") or ""
        priority = (fields.get("priority") or {}).get("name") or ""
        labels = list(fields.get("labels") or [])
        return Ticket(
            key=str(issue.get("key") or ""),
            title=str(fields.get("summary") or "")[:200],
            reporter=reporter,
            assignee=assignee,
            status=status,
            kind=kind,
            priority=priority,
            created_at=str(fields.get("created") or ""),
            updated_at=str(fields.get("updated") or ""),
            labels=labels,
            url=str(issue.get("self") or ""),
        )
