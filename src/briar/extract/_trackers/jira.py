"""Jira `TrackerProvider`.

Backed by ``atlassian-python-api``'s `Jira` client (same library that
backs `BitbucketProvider`). The site and the auth come from the company
login (`[auth.jira]`, spec 005): ``JiraTokenAuth`` (email + API token, the
default) or ``JiraCookieAuth`` (the pasted browser `Cookie:` header). See
`_jira_auth.py` for the strategy contract."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from functools import partial
from typing import Any, Dict, List, Optional, Sequence

from briar.auth.check import LoginRejected
from briar.auth.logins import CompanyLogins, JiraLogin
from briar.decorators import swallow_errors
from briar.extract._login_failures import RequestsFailures
from briar.extract._tracker import Comment, Ticket, TrackerProvider
from briar.extract._trackers._jira_auth import JiraAuths, JiraAuthStrategy


log = logging.getLogger(__name__)


# Standard Jira project key: starts with uppercase letter, then letters
# digits and underscore. Validate at the JQL boundary so a malicious
# project value can't escape the f-string into the JQL query.
_PROJECT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# ADF node types that should emit a trailing newline during plain-text
# flattening. Membership-style check that replaces the open-coded tuple
# inline in `_adf_walk`.
_ADF_BLOCK_NODES = frozenset(
    {
        "paragraph",
        "heading",
        "bulletList",
        "orderedList",
        "listItem",
        "codeBlock",
    }
)


class JiraTracker(TrackerProvider):
    kind = "jira"
    login_source = "jira"
    TICKET_FIELDS = ("summary", "status", "issuetype", "priority", "reporter", "assignee", "labels", "created", "updated")
    # The most results one Jira search page gives.
    PAGE_LIMIT = 100

    def __init__(self, *, logins: Optional[CompanyLogins] = None, auth: Optional[JiraAuthStrategy] = None) -> None:
        """`auth` (tests) wins over the strategy of the login method."""
        self._logins = logins
        self._auth = auth
        self._client = None

    def _login(self) -> JiraLogin:
        return (self._logins or CompanyLogins.defaults("")).jira()

    def _jira(self):
        if self._client is None:
            from atlassian import Jira

            login = self._login()
            kwargs = (self._auth or JiraAuths.for_login(login)).configure(login)
            # timeout=30: same as the messaging-side Jira clients (Phase 2);
            # extract-side was missed until the re-audit.
            self._client = Jira(url=login.site_url(), cloud=True, timeout=30, **kwargs)
        return self._client

    def is_available(self) -> bool:
        """The login is the job of the check before the run (spec 005, J15)."""
        return True

    def who_am_i(self) -> str:
        return RequestsFailures().account(self._myself)

    def read(self, resource: str) -> None:
        if not _PROJECT_RE.match(resource):
            raise LoginRejected(f"{resource!r} is not a Jira project key")
        RequestsFailures().read(partial(self._jira().get_project, resource))

    def _myself(self) -> str:
        profile = self._jira().myself()
        return str(profile.get("emailAddress") or profile.get("displayName") or profile.get("accountId") or "")

    def assigned(self, project: str, max_count: int) -> List[Ticket]:
        """Open tickets of `project` assigned to the account of the login, highest priority
        first, then the oldest (`briar serve`, spec 003 S9 and S14). Errors are not swallowed."""
        if not _PROJECT_RE.match(project):
            raise LoginRejected(f"{project!r} is not a Jira project key")
        jql = f'project = "{project}" AND assignee = currentUser() AND statusCategory != "Done" ORDER BY priority DESC, created ASC'
        payload = {"jql": jql, "fields": list(self.TICKET_FIELDS), "maxResults": min(max_count, self.PAGE_LIMIT)}
        found = self._jira().post("rest/api/3/search/jql", data=payload)
        return [self._to_ticket(issue) for issue in (found or {}).get("issues", [])[:max_count]]

    def comment(self, key: str, text: str) -> None:
        """A comment on a ticket (`briar serve`, spec 003 S19)."""
        RequestsFailures().read(partial(self._jira().issue_add_comment, key, text))

    def move(self, key: str, status: str) -> None:
        """Move a ticket to the status named in the runbook (S26)."""
        RequestsFailures().read(partial(self._jira().set_issue_status, key, status))

    def can_write(self, project: str, permissions: Sequence[str]) -> None:
        """Raise `LoginRejected` when the login lacks one of `permissions` in `project`
        (`mypermissions`, nothing is written; spec 003 S29)."""
        RequestsFailures().read(partial(self._require_permissions, project, permissions))

    def _require_permissions(self, project: str, permissions: Sequence[str]) -> None:
        found = self._jira().get("rest/api/2/mypermissions", params={"projectKey": project, "permissions": ",".join(permissions)})
        granted = {name for name, entry in ((found or {}).get("permissions") or {}).items() if entry.get("havePermission")}
        missing = [name for name in permissions if name not in granted]
        if missing:
            raise LoginRejected(f"missing the Jira permission {', '.join(missing)}")

    @swallow_errors(default=[], message="jira list_tickets")
    def list_tickets(self, project: str, *, state: str, max_count: int) -> List[Ticket]:
        if not _PROJECT_RE.match(project):
            raise ValueError(f"jira project key must match ^[A-Z][A-Z0-9_]*$: got {project!r}")
        # Jira state vocabulary uses `statusCategory`: To Do | In Progress | Done.
        # Map "open" → not Done; "closed" → Done.
        if state == "closed":
            jql = f'project = "{project}" AND statusCategory = "Done" ORDER BY updated DESC'
        else:
            jql = f'project = "{project}" AND statusCategory != "Done" ORDER BY updated DESC'
        # Atlassian removed /rest/api/2/search per CHANGE-2046. The v3 enhanced-search
        # endpoint is POST-only and requires explicit `fields`; ask for what _to_ticket reads.
        payload = {
            "jql": jql,
            "fields": ["summary", "status", "issuetype", "priority", "reporter", "assignee", "labels", "created", "updated"],
            "maxResults": min(max_count, 100),
        }
        result = self._jira().post("rest/api/3/search/jql", data=payload)
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
        return replace(ticket, description=str(description or "")[:8000])

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
            if kind in _ADF_BLOCK_NODES:
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
