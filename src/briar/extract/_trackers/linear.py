"""Linear `TrackerProvider` — stub.

Linear uses a GraphQL API. Implementing requires either the
``gql`` Python client + an explicit query for each verb, or the
unofficial ``linear-sdk-python`` package. Neither is currently a
dependency. ``is_available()`` reports True iff ``LINEAR_<COMPANY>_TOKEN``
is set; every data verb raises ``NotImplementedError`` with the
GraphQL query that needs wiring.

This mirrors the early-stage BitbucketProvider — better a loud
NotImplementedError on first call than an empty extract that the
operator only notices later."""

from __future__ import annotations

import logging
from typing import List

from briar.env_vars import CredEnv
from briar.extract._tracker import Comment, Ticket, TrackerProvider


log = logging.getLogger(__name__)


class LinearTracker(TrackerProvider):
    kind = "linear"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._token = CredEnv.LINEAR_TOKEN.read(company=company) if company else ""

    def is_available(self) -> bool:
        return bool(self._token)

    def list_tickets(self, project: str, *, state: str, max_count: int) -> List[Ticket]:
        raise NotImplementedError(
            "LinearTracker.list_tickets is not implemented yet. Linear uses a "
            "GraphQL API at https://api.linear.app/graphql . Auth header is "
            "`Authorization: <token>` (no 'Bearer'). Query the `issues` "
            "connection filtered by `team: { key: { eq: \"<project>\" } }` "
            "and `state: { type: { eq: \"started\"|\"backlog\"|\"unstarted\"|\"completed\" } }`. "
            "Map response onto _tracker.Ticket: key<-identifier, title<-title, "
            "reporter<-creator.name, assignee<-assignee.name, status<-state.name, "
            "labels<-labels.nodes[].name."
        )

    def list_comments(self, project: str, ticket_key: str) -> List[Comment]:
        raise NotImplementedError(
            "LinearTracker.list_comments — GraphQL `issue(id:).comments.nodes` "
            "with fields `body`, `user.name`, `createdAt`."
        )
