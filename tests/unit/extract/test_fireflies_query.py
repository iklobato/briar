"""The `transcripts` query of the Fireflies adapter declares the variable types the live API
takes. Found on 2026-11-12 in a real run: the API answered `400 GRAPHQL_VALIDATION_FAILED`
("Unknown type TranscriptsQueryScope", "$participants of type [String] used in position
expecting type [String!]"), and `@swallow_errors` turned every meeting list into "no data".

The types below come from an introspection of `https://api.fireflies.ai/graphql` that day:
`transcripts(keyword: String, scope: String, fromDate: DateTime, toDate: DateTime, limit: Int,
skip: Int, participants: [String!], ...)`."""

from __future__ import annotations

import re
from typing import ClassVar, Dict

from briar.extract._meetings.fireflies import _LIST_QUERY


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
