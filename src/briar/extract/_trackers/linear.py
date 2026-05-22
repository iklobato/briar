"""Linear `TrackerProvider`.

Linear uses a GraphQL API at ``https://api.linear.app/graphql``. Auth
is a personal API key in the ``Authorization`` header (NO ``Bearer``
prefix — Linear-specific quirk). Implemented via stdlib ``urllib`` so
no new dependency.

``project`` is the team key (e.g. ``ENG``, ``DESIGN``) — Linear's
team key, not issue identifier."""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Dict, List

from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.extract._tracker import Comment, Ticket, TrackerProvider


log = logging.getLogger(__name__)


_ENDPOINT = "https://api.linear.app/graphql"


# Linear's `state.type` vocabulary maps closely to the abstraction's
# open/closed states.
_STATE_TYPES_OPEN = ("triage", "backlog", "unstarted", "started")
_STATE_TYPES_CLOSED = ("completed", "cancelled")


class LinearTracker(TrackerProvider):
    kind = "linear"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._token = CredEnv.LINEAR_TOKEN.read(company=company) if company else ""

    def is_available(self) -> bool:
        return bool(self._token)

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        if not company:
            return []
        return [CredEnv.LINEAR_TOKEN.for_company(company)]

    @swallow_errors(default=[], message="linear list_tickets")
    def list_tickets(self, project: str, *, state: str, max_count: int) -> List[Ticket]:
        states = _STATE_TYPES_CLOSED if state == "closed" else _STATE_TYPES_OPEN
        query = """
        query Issues($team: String!, $stateTypes: [String!], $first: Int!) {
          issues(
            first: $first,
            orderBy: updatedAt,
            filter: { team: { key: { eq: $team } }, state: { type: { in: $stateTypes } } }
          ) {
            nodes {
              identifier title createdAt updatedAt url
              priorityLabel
              state { name type }
              creator { displayName name }
              assignee { displayName name }
              labels { nodes { name } }
            }
          }
        }
        """
        result = self._gql(query, {"team": project, "stateTypes": list(states), "first": max_count})
        nodes = ((result.get("data") or {}).get("issues") or {}).get("nodes") or []
        out: List[Ticket] = []
        for node in nodes:
            out.append(self._to_ticket(node))
        return out

    @swallow_errors(default=[], message="linear list_comments")
    def list_comments(self, project: str, ticket_key: str) -> List[Comment]:
        # Linear's GraphQL accepts the human identifier directly.
        query = """
        query IssueComments($id: String!) {
          issue(id: $id) {
            comments(first: 50, orderBy: createdAt) {
              nodes { body createdAt user { displayName name } }
            }
          }
        }
        """
        result = self._gql(query, {"id": ticket_key})
        nodes = (((result.get("data") or {}).get("issue") or {}).get("comments") or {}).get("nodes") or []
        out: List[Comment] = []
        for c in nodes:
            user = c.get("user") or {}
            author = str(user.get("displayName") or user.get("name") or "")
            out.append(Comment(author=author, body=str(c.get("body") or "")[:500], created_at=str(c.get("createdAt") or "")))
        return out

    @swallow_errors(default=None, message="linear get_ticket")
    def get_ticket(self, project: str, ticket_key: str) -> Ticket:
        query = """
        query Issue($id: String!) {
          issue(id: $id) {
            identifier title description createdAt updatedAt url
            priorityLabel
            state { name type }
            creator { displayName name }
            assignee { displayName name }
            labels { nodes { name } }
          }
        }
        """
        result = self._gql(query, {"id": ticket_key})
        node = (result.get("data") or {}).get("issue")
        if not isinstance(node, dict):
            return super().get_ticket(project, ticket_key)
        ticket = self._to_ticket(node)
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
            description=str(node.get("description") or "")[:8000],
        )

    # ---- internals --------------------------------------------------------

    def _gql(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        req = urllib.request.Request(
            _ENDPOINT,
            data=payload,
            headers={
                "Authorization": self._token,  # NO "Bearer " — Linear-specific
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("errors"):
            log.warning("linear graphql errors: %s", body["errors"])
        return body

    @staticmethod
    def _to_ticket(node: Dict[str, Any]) -> Ticket:
        creator = node.get("creator") or {}
        assignee = node.get("assignee") or {}
        state = node.get("state") or {}
        labels_node = (node.get("labels") or {}).get("nodes") or []
        labels = [str(l.get("name") or "") for l in labels_node if l.get("name")]
        return Ticket(
            key=str(node.get("identifier") or ""),
            title=str(node.get("title") or "")[:200],
            reporter=str(creator.get("displayName") or creator.get("name") or ""),
            assignee=str(assignee.get("displayName") or assignee.get("name") or ""),
            status=str(state.get("name") or ""),
            kind="",  # Linear doesn't have a native issue-type concept
            priority=str(node.get("priorityLabel") or ""),
            created_at=str(node.get("createdAt") or ""),
            updated_at=str(node.get("updatedAt") or ""),
            labels=labels,
            url=str(node.get("url") or ""),
        )
