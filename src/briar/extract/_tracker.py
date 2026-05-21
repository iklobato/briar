"""`TrackerProvider` — vendor-neutral facade for issue/ticket systems.

Symmetric to `RepositoryProvider`. Where the repo provider abstracts
code hosts (GitHub, Bitbucket, GitLab), this one abstracts trackers
(Jira, GitHub Issues, Bitbucket Issues, Linear, …). Same Strategy +
Registry shape; each concrete adapter lives in `_trackers/`.

Why this is a separate ABC from RepositoryProvider: trackers and
repos overlap in vendor (GitHub hosts both) but have different
verbs and shapes. A ticket is not a PR; a comment thread on a ticket
is not the same shape as PR review comments. Mixing them into one
ABC would put GitHub at the centre of the design — exactly the
coupling we are removing.

Extractors that consume tickets: `active-tickets` (open tickets agents
should avoid duplicating), `ticket-archaeology` (median time-to-close,
top assignees, label distribution)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, List


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ticket:
    """Vendor-neutral ticket shape.

    Maps Jira `key` / `summary` / `reporter.displayName` /
    `assignee.displayName` / `status.name` / `issuetype.name` /
    `priority.name` / `created` / `updated`; GitHub Issues
    `number` / `title` / `user.login` / `assignees[].login` /
    `state` / labels-as-types; Bitbucket Issues `id` / `title` /
    `reporter.display_name` / `assignee.display_name` / `state` /
    `kind`; Linear `identifier` / `title` / `creator.name` /
    `assignee.name` / `state.name` / labels-as-types — onto the same
    eight fields below."""

    key: str  # Jira `PROJ-123`; GH `#42`; Linear `ENG-7`; BB `#5`
    title: str
    reporter: str
    assignee: str
    status: str  # open / in_progress / done / closed (provider's vocabulary)
    kind: str  # bug / feature / task / story (provider's vocabulary)
    priority: str
    created_at: str  # ISO-8601
    updated_at: str = ""
    labels: List[str] = field(default_factory=list)
    url: str = ""
    # Full markdown body, populated by `get_ticket` (single-ticket fetch).
    # The list/scan verbs (`list_tickets`) leave this empty — they don't
    # fetch the body to avoid N round-trips.
    description: str = ""


@dataclass(frozen=True)
class Comment:
    """One comment on a ticket. Used by extractors that need the
    discussion-density signal (a 20-comment ticket means contention)."""

    author: str
    body: str
    created_at: str


class TrackerProvider(ABC):
    """Strategy contract. Concrete subclasses adapt one vendor onto
    these verbs.

    Two abstract verbs (`is_available`, `list_tickets`) — every
    provider must support them. Two concrete-default-empty verbs
    (`list_comments`, `list_status_transitions`) so providers without
    a native concept degrade to empty results, never exceptions."""

    kind: ClassVar[str] = ""

    @abstractmethod
    def is_available(self) -> bool:
        """True iff credentials are present and the provider is usable
        for the company this provider was built for."""

    @abstractmethod
    def list_tickets(self, project: str, *, state: str, max_count: int) -> List[Ticket]:
        """List tickets in a project. ``state`` is ``"open"`` |
        ``"closed"`` (provider translates onto its own vocabulary).
        ``project`` is the project key (Jira `PROJ`, GH `owner/repo`,
        Linear team key, BB `workspace/repo`). Most-recent first."""

    def list_comments(self, project: str, ticket_key: str) -> List[Comment]:
        """Return comments on one ticket. Empty default."""
        return []

    def list_status_transitions(self, project: str, ticket_key: str) -> List[str]:
        """Return the ticket's status-history names in chronological
        order (e.g. ``["open", "in_progress", "in_review", "done"]``).
        Empty default — only providers with a native changelog
        (Jira, Linear) implement this."""
        return []

    def get_ticket(self, project: str, ticket_key: str) -> Ticket:
        """Fetch one ticket by key WITH its full description body
        populated. Used by `FetchTicketContext` at agent-invocation
        time. Default returns an empty Ticket; concrete providers
        override to hit their single-ticket endpoint."""
        return Ticket(
            key=ticket_key,
            title="",
            reporter="",
            assignee="",
            status="",
            kind="",
            priority="",
            created_at="",
        )
