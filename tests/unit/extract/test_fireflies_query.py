"""The `transcripts` query of the Fireflies adapter declares the variable types the live API
takes. Found on 2026-11-12 in a real run: the API answered `400 GRAPHQL_VALIDATION_FAILED`
("Unknown type TranscriptsQueryScope", "$participants of type [String] used in position
expecting type [String!]"), and `@swallow_errors` turned every meeting list into "no data".

The types below come from an introspection of `https://api.fireflies.ai/graphql` that day:
`transcripts(keyword: String, scope: String, fromDate: DateTime, toDate: DateTime, limit: Int,
skip: Int, participants: [String!], ...)`."""

from __future__ import annotations

import io
import json
import re
import urllib.request
from typing import Any, ClassVar, Dict, List

import pytest

from briar.extract._meeting import MeetingPage
from briar.extract._meetings.fireflies import _LIST_QUERY, FirefliesMeetingProvider
from tests.unit.fake_logins import FakeLogins

# Fireflies gives at most 50 meetings per call.
PAGE = 50


class Declared:
    """The `$name: Type` declarations of a GraphQL operation."""

    DECLARATION: ClassVar["re.Pattern[str]"] = re.compile(r"\$(\w+)\s*:\s*([\w\[\]!]+)")

    def __init__(self, query: str) -> None:
        self._query = query

    def types(self) -> Dict[str, str]:
        header = self._query.split(")", 1)[0]
        return dict(self.DECLARATION.findall(header))


class TestTranscriptsQuery:
    LIVE: ClassVar[Dict[str, str]] = {
        "keyword": "String",
        "fromDate": "DateTime",
        "toDate": "DateTime",
        "limit": "Int",
        "skip": "Int",
        "participants": "[String!]",
        "scope": "String",
    }

    def test_every_variable_has_the_type_of_the_live_schema(self) -> None:
        assert Declared(_LIST_QUERY).types() == self.LIVE


class FakeFireflies:
    """Stands in for `urllib.request.urlopen`: keeps each GraphQL body, answers one page."""

    def __init__(self, nodes: List[Dict[str, Any]]) -> None:
        self.bodies: List[Dict[str, Any]] = []
        self._nodes = nodes

    def urlopen(self, request: urllib.request.Request, timeout: float) -> io.BytesIO:
        self.bodies.append(json.loads(request.data))  # type: ignore[arg-type]
        return io.BytesIO(json.dumps({"data": {"transcripts": self._nodes}}).encode())


class TestTranscriptsPage:
    def test_a_page_asks_from_skip_and_reads_the_speakers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The company filter (F8) reads pages with `skip` and matches speakers to members."""
        fake = FakeFireflies([{"id": "m1", "title": "Sync", "speakers": [{"name": "Bo Wang"}, {"name": None}]}])
        monkeypatch.setattr("urllib.request.urlopen", fake.urlopen)
        provider = FirefliesMeetingProvider(logins=FakeLogins(environ={"BRIAR_ACME_FIREFLIES_API_KEY": "ff_xxx"}).logins())

        meetings = provider.list_meetings(MeetingPage("2026-11-01T00:00:00+00:00", "2026-11-12T00:00:00+00:00", PAGE, PAGE))

        assert [meeting.speakers for meeting in meetings] == [["Bo Wang", ""]]
        assert {key: fake.bodies[0]["variables"].get(key) for key in ("limit", "skip", "participants")} == {"limit": PAGE, "skip": PAGE, "participants": None}
