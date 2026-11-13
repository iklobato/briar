"""GitHub Issues `TrackerProvider`.

Reuses the same `GithubApi` facade as the GitHub repo provider — no
extra dependency. GitHub Issues use ``state=open|closed`` natively;
labels and assignees map onto the `Ticket` shape directly. ``project``
here is ``<owner>/<repo>`` to match every other GH call site."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Dict, List, Optional

from briar.auth.logins import CompanyLogins, GithubLogin
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
    login_source = "github"
    project_is_repo_slug = True

    def __init__(self, *, logins: Optional[CompanyLogins] = None) -> None:
        self._logins = logins

    def is_available(self) -> bool:
        """The login is the job of the check before the run (spec 005, J15)."""
        return True

    def who_am_i(self) -> str:
        return self._api().account()

    def assigned(self, repo: str, login: str) -> List[Ticket]:
        """Open issues of `repo` assigned to `login`, pull requests left out (`briar serve`,
        spec 003 S9). Errors are not swallowed."""
        rows = self._api().get_paginated(f"/repos/{repo}/issues?state=open&assignee={login}&sort=created&direction=asc")
        return [self._to_ticket(row, repo) for row in rows if "pull_request" not in row]

    def read(self, resource: str) -> None:
        self._api().read_repo(resource)

    def _login(self) -> GithubLogin:
        """The GitHub login of the company (spec 005); with no logins, the defaults of no company."""
        return (self._logins or CompanyLogins.defaults("")).github()

    def _api(self) -> GithubApi:
        return GithubApi(self._login().token())

    @swallow_errors(default=[], message="github-issues list_tickets")
    def list_tickets(self, project: str, *, state: str, max_count: int) -> List[Ticket]:
        gh_state = "closed" if state == "closed" else "open"
        pages_needed = max(1, (max_count // 100) + 1)
        # GitHub's /issues endpoint returns BOTH issues and PRs; filter out PRs.
        rows = self._api().get_paginated(
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
        rows = self._api().get_paginated(
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
        data = self._api().get_json(f"/repos/{project}/issues/{number}")
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
        events = self._api().get_paginated(
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
            open_blockers=int((issue.get("issue_dependencies_summary") or {}).get("blocked_by") or 0),
            unblocks=int((issue.get("issue_dependencies_summary") or {}).get("blocking") or 0),
        )
