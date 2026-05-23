"""Jira board reader.

Handles two URL shapes:

  * `https://<tenant>.atlassian.net/jira/software/projects/<KEY>/boards/<id>`
  * `https://<tenant>.atlassian.net/jira/software/c/projects/<KEY>/boards/<id>`

…plus the short form `jira:<KEY>` for headless callers. Card data is
fetched through the existing `JiraTracker` (`TrackerProvider`) so this
file owns the URL-parsing concern, not the Jira API."""

from __future__ import annotations

import logging
import re
from typing import List

from briar.errors import CliError
from briar.extract._tracker import Ticket
from briar.extract._trackers import make_tracker
from briar.plan._board import BoardReader, BoardRef
from briar.plan._models import PlanCard


log = logging.getLogger(__name__)


_URL_RE = re.compile(
    r"^https?://(?P<tenant>[^/]+)/jira/software/(?:c/)?projects/(?P<project>[^/]+)/boards/(?P<board>\d+)",
    re.IGNORECASE,
)
_SHORT_RE = re.compile(r"^jira:(?P<project>[A-Za-z0-9_\-]+)$")
_DEP_LINE_RE = re.compile(
    r"(?:depends on|blocked by|requires|after)\s*[:\-]?\s*([A-Z][A-Z0-9_]+-\d+)",
    re.IGNORECASE,
)


class JiraBoardReader(BoardReader):
    kind = "jira"

    def matches(self, url: str) -> bool:
        return bool(_URL_RE.match(url or "")) or bool(_SHORT_RE.match(url or ""))

    def parse(self, url: str) -> BoardRef:
        url = (url or "").strip()
        short = _SHORT_RE.match(url)
        if short:
            return BoardRef(
                tracker="jira",
                project=short.group("project"),
                url=url,
            )
        m = _URL_RE.match(url)
        if not m:
            raise CliError(f"jira board URL not recognised: {url!r}")
        return BoardRef(
            tracker="jira",
            project=m.group("project"),
            url=url,
            base_url=f"https://{m.group('tenant')}",
            extras=(("board_id", m.group("board")),),
        )

    def fetch(self, ref: BoardRef, *, company: str, max_cards: int) -> List[PlanCard]:
        tracker = make_tracker("jira", company=company)
        if not tracker.is_available():
            raise CliError(
                "jira tracker is not available — set JIRA_URL + auth env vars "
                f"for company={company!r} (see `briar secrets doctor`)."
            )
        tickets = tracker.list_tickets(ref.project, state="open", max_count=max_cards)
        cards: List[PlanCard] = []
        for stub in tickets:
            full = tracker.get_ticket(ref.project, stub.key) or stub
            ticket = full if (full.description or full.title) else stub
            cards.append(self._to_card(ticket))
        return cards

    @staticmethod
    def _to_card(ticket: Ticket) -> PlanCard:
        body = ticket.description or ""
        explicit_deps: List[str] = []
        for match in _DEP_LINE_RE.finditer(body):
            key = match.group(1).upper()
            if key not in explicit_deps and key != ticket.key:
                explicit_deps.append(key)
        return PlanCard(
            key=ticket.key,
            title=ticket.title,
            url=ticket.url,
            tracker="jira",
            summary=body[:1500],
            depends_on=explicit_deps,
            sources=[f"jira:{ticket.key}"] if ticket.url else [],
            notes="",
        )
