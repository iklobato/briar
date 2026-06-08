"""Jira board reader — the `fetch` path that turns Jira tickets into PlanCards.

`JiraBoardReader.fetch` owns URL/short-form parsing and the per-card
normalisation; the actual Jira REST round-trip is delegated to the existing
`JiraTracker` (`TrackerProvider`) via `make_tracker`. So this module's own
logic is best exercised by mocking `make_tracker` to return a fake tracker
and asserting the normalised `PlanCard`s — the tracker's REST behaviour is
covered by the tracker's own tests.

Ticket / board shapes follow the Jira Cloud Agile REST contract:
  * Boards / sprints — https://developer.atlassian.com/cloud/jira/software/rest/
  * Issue search — https://developer.atlassian.com/cloud/jira/platform/rest/v3/
"""

from __future__ import annotations

import pytest

from briar.errors import CliError
from briar.extract._tracker import Ticket
from briar.plan._boards.jira_board import JiraBoardReader

pytestmark = pytest.mark.boundary


def _ticket(key, title, *, description="", url="") -> Ticket:
    """Vendor-neutral Ticket as `JiraTracker._to_ticket` would emit it.
    Maps Jira `key` / `fields.summary` / `fields.description` / browse URL.
    """
    return Ticket(
        key=key,
        title=title,
        reporter="alice",
        assignee="bob",
        status="open",
        kind="task",
        priority="Medium",
        created_at="2026-01-01T00:00:00.000+0000",
        url=url,
        description=description,
    )


class _FakeTracker:
    """Stand-in `JiraTracker`. Records calls so we can assert the reader
    passes the right project/state/max_count through to the tracker."""

    def __init__(self, *, available=True, stubs=None, full=None) -> None:
        self._available = available
        self._stubs = stubs or []
        self._full = full or {}
        self.list_calls = []
        self.get_calls = []

    def is_available(self) -> bool:
        return self._available

    def list_tickets(self, project, *, state, max_count):
        self.list_calls.append((project, state, max_count))
        return list(self._stubs)

    def get_ticket(self, project, key):
        self.get_calls.append((project, key))
        return self._full.get(key)


def _patch_tracker(mocker, tracker):
    return mocker.patch("briar.plan._boards.jira_board.make_tracker", return_value=tracker)


def _ref():
    return JiraBoardReader().parse("jira:KAN")


class TestFetchNormalisation:
    def test_cards_normalised_from_full_tickets(self, mocker):
        stub = _ticket("KAN-1", "stub title")
        full = _ticket(
            "KAN-1",
            "Add login",
            description="Wire up OAuth.\n\nDepends on: KAN-2\nBlocked by KAN-3",
            url="https://x.atlassian.net/browse/KAN-1",
        )
        tracker = _FakeTracker(stubs=[stub], full={"KAN-1": full})
        make = _patch_tracker(mocker, tracker)

        cards = JiraBoardReader().fetch(_ref(), company="acme", max_cards=25)

        make.assert_called_once_with("jira", company="acme")
        assert tracker.list_calls == [("KAN", "open", 25)]
        assert len(cards) == 1
        card = cards[0]
        assert card.key == "KAN-1"
        assert card.title == "Add login"  # full ticket wins over stub
        assert card.tracker == "jira"
        assert card.url == "https://x.atlassian.net/browse/KAN-1"
        assert card.summary == "Wire up OAuth.\n\nDepends on: KAN-2\nBlocked by KAN-3"
        # Dep lines parsed + upper-cased; self-ref excluded.
        assert card.depends_on == ["KAN-2", "KAN-3"]
        assert card.sources == ["jira:KAN-1"]

    def test_falls_back_to_stub_when_full_fetch_empty(self, mocker):
        # get_ticket returns a ticket with no title/description → reader keeps
        # the stub it already had from list_tickets.
        stub = _ticket("KAN-7", "stub title", url="https://x/browse/KAN-7")
        empty = _ticket("KAN-7", "", description="")
        tracker = _FakeTracker(stubs=[stub], full={"KAN-7": empty})
        _patch_tracker(mocker, tracker)

        cards = JiraBoardReader().fetch(_ref(), company="acme", max_cards=10)
        assert cards[0].title == "stub title"

    def test_falls_back_to_stub_when_get_ticket_returns_none(self, mocker):
        stub = _ticket("KAN-9", "stub only", url="https://x/browse/KAN-9")
        tracker = _FakeTracker(stubs=[stub], full={"KAN-9": None})
        _patch_tracker(mocker, tracker)

        cards = JiraBoardReader().fetch(_ref(), company="acme", max_cards=10)
        assert cards[0].title == "stub only"
        assert cards[0].depends_on == []

    def test_ticket_without_url_yields_no_sources(self, mocker):
        # `sources` only populated when the ticket carries a browse URL.
        full = _ticket("KAN-2", "No URL ticket", description="body", url="")
        tracker = _FakeTracker(stubs=[full], full={"KAN-2": full})
        _patch_tracker(mocker, tracker)

        cards = JiraBoardReader().fetch(_ref(), company="acme", max_cards=10)
        assert cards[0].sources == []

    def test_empty_board_returns_empty(self, mocker):
        tracker = _FakeTracker(stubs=[])
        _patch_tracker(mocker, tracker)
        assert JiraBoardReader().fetch(_ref(), company="acme", max_cards=10) == []

    def test_summary_truncated_to_1500(self, mocker):
        long_body = "x" * 5000
        full = _ticket("KAN-5", "Long", description=long_body, url="https://x/browse/KAN-5")
        tracker = _FakeTracker(stubs=[full], full={"KAN-5": full})
        _patch_tracker(mocker, tracker)
        cards = JiraBoardReader().fetch(_ref(), company="acme", max_cards=10)
        assert len(cards[0].summary) == 1500

    def test_dedupes_and_excludes_self_dep(self, mocker):
        full = _ticket(
            "KAN-4",
            "Self ref",
            description="Depends on KAN-4\nrequires KAN-8\nafter KAN-8",
            url="https://x/browse/KAN-4",
        )
        tracker = _FakeTracker(stubs=[full], full={"KAN-4": full})
        _patch_tracker(mocker, tracker)
        cards = JiraBoardReader().fetch(_ref(), company="acme", max_cards=10)
        # KAN-4 (self) excluded; KAN-8 listed once despite two matches.
        assert cards[0].depends_on == ["KAN-8"]


class TestFailureModes:
    def test_unavailable_tracker_raises(self, mocker):
        # Missing JIRA_URL / auth → tracker.is_available() False → CliError
        # before any list_tickets call.
        tracker = _FakeTracker(available=False)
        _patch_tracker(mocker, tracker)
        with pytest.raises(CliError, match="jira tracker is not available"):
            JiraBoardReader().fetch(_ref(), company="acme", max_cards=10)
        assert tracker.list_calls == []

    def test_full_url_drives_project_and_board(self, mocker):
        # The board id from the URL is parsed but fetch keys off project.
        ref = JiraBoardReader().parse("https://acme.atlassian.net/jira/software/projects/ENG/boards/12")
        assert ref.project == "ENG"
        assert ref.extra("board_id") == "12"
        tracker = _FakeTracker(stubs=[])
        _patch_tracker(mocker, tracker)
        JiraBoardReader().fetch(ref, company="acme", max_cards=10)
        assert tracker.list_calls == [("ENG", "open", 10)]
