"""GitHub Projects v2 board reader — the GraphQL fetch + normalisation path.

The reader POSTs a GraphQL query directly through `urllib.request.urlopen`
(Projects v2 is GraphQL-only, no PyGithub v3 surface), so we mock at that
seam and assert the *normalised* `PlanCard`s the reader produces from a
documented response shape — not merely that a request happened.

GraphQL response shapes are modelled on:
  * Projects v2 objects — https://docs.github.com/en/graphql/reference/objects#projectv2
  * Managing projects via the API —
    https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects
The GraphQL error envelope `{"errors":[{"message": ...}]}` is documented at
  * https://docs.github.com/en/graphql/guides/introduction-to-graphql#discovering-the-graphql-api
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from briar.errors import CliError
from briar.plan._boards.github_project import GithubProjectBoardReader, _field_values

pytestmark = pytest.mark.boundary


# ─── fixtures: documented Projects v2 response shapes ───────────────────


def _resp_body(nodes):
    """Wrap item nodes in the documented `{"data":{"organization":{"projectV2":
    {"title":..,"items":{"nodes":[...]}}}}}` envelope.

    Models the shape returned by a `organization(login:..){projectV2(number:..)
    {items(first:..){nodes{...}}}}` query — see GitHub Projects v2 API docs.
    """
    return {
        "data": {
            "organization": {
                "projectV2": {
                    "title": "Roadmap",
                    "items": {"nodes": nodes},
                }
            }
        }
    }


# A linked Issue item, with a Status single-select field value + a Labels
# chip, modelled on ProjectV2ItemFieldSingleSelectValue / ...TextValue nodes.
_ISSUE_NODE = {
    "id": "PVTI_item1",
    "type": "ISSUE",
    "fieldValues": {
        "nodes": [
            {"__typename": "ProjectV2ItemFieldTextValue"},  # no field/value → skipped
            {
                "__typename": "ProjectV2ItemFieldSingleSelectValue",
                "field": {"name": "Status"},
                "name": "In Progress",
            },
            {
                "__typename": "ProjectV2ItemFieldSingleSelectValue",
                "field": {"name": "Labels"},
                "name": "backend",
            },
        ]
    },
    "content": {
        "__typename": "Issue",
        "number": 1400,
        "title": "Refactor profile model imports",
        "body": "Tidy the import graph.\n\nDepends on #1399",
        "url": "https://github.com/bitspark-co/widgets/issues/1400",
        "state": "OPEN",
        "repository": {"nameWithOwner": "bitspark-co/widgets"},
    },
}

_DRAFT_NODE = {
    "id": "PVTI_draft9",
    "type": "DRAFT_ISSUE",
    "fieldValues": {"nodes": []},
    "content": {
        "__typename": "DraftIssue",
        "title": "Spike: evaluate caching",
        "body": "",
    },
}


class _FakeHTTPResponse:
    """Context-manager stand-in for `urllib.request.urlopen`'s return."""

    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._raw


@pytest.fixture
def gh_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_testtoken")


def _ref():
    return GithubProjectBoardReader().parse("https://github.com/orgs/bitspark-co/projects/7")


# ─── happy path: normalisation ─────────────────────────────────────────


class TestFetchNormalisation:
    def test_issue_and_draft_cards_normalised(self, mocker, gh_env):
        mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            return_value=_FakeHTTPResponse(_resp_body([_ISSUE_NODE, _DRAFT_NODE])),
        )
        cards = GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=50)

        assert [c.key for c in cards] == ["bitspark-co/widgets#1400", "draft:Spike: evaluate caching"]
        issue = cards[0]
        assert issue.title == "Refactor profile model imports"
        assert issue.url == "https://github.com/bitspark-co/widgets/issues/1400"
        assert issue.tracker == "github-project"
        # Explicit dep parsed out of the body, namespaced to the repo.
        assert issue.depends_on == ["bitspark-co/widgets#1399"]
        assert issue.sources == ["github:https://github.com/bitspark-co/widgets/issues/1400"]
        # Status chip + labels folded into notes.
        assert issue.notes == "status=In Progress; labels=backend"

        draft = cards[1]
        assert draft.title == "Spike: evaluate caching"
        assert draft.url == ""
        assert draft.depends_on == []
        assert draft.sources == []
        assert draft.notes == ""

    def test_request_shape_is_graphql_post_with_bearer(self, mocker, gh_env):
        urlopen = mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            return_value=_FakeHTTPResponse(_resp_body([_ISSUE_NODE])),
        )
        GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=50)

        req = urlopen.call_args.args[0]
        assert req.full_url == "https://api.github.com/graphql"
        assert req.method == "POST"
        assert req.get_header("Authorization") == "Bearer ghp_testtoken"
        sent = json.loads(req.data.decode("utf-8"))
        # Query targets `organization(...)` because the URL scope is `orgs`.
        assert "organization(login: $login)" in sent["query"]
        assert sent["variables"] == {"login": "bitspark-co", "number": 7, "first": 50}

    def test_user_scope_uses_user_root(self, mocker, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
        urlopen = mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            return_value=_FakeHTTPResponse({"data": {"user": {"projectV2": {"title": "P", "items": {"nodes": [_DRAFT_NODE]}}}}}),
        )
        ref = GithubProjectBoardReader().parse("https://github.com/users/octocat/projects/3")
        cards = GithubProjectBoardReader().fetch(ref, company="acme", max_cards=10)
        sent = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        assert "user(login: $login)" in sent["query"]
        assert cards[0].key == "draft:Spike: evaluate caching"

    def test_first_is_clamped_to_100(self, mocker, gh_env):
        urlopen = mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            return_value=_FakeHTTPResponse(_resp_body([])),
        )
        GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=5000)
        sent = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        assert sent["variables"]["first"] == 100

    def test_first_is_floored_to_one(self, mocker, gh_env):
        urlopen = mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            return_value=_FakeHTTPResponse(_resp_body([])),
        )
        GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=0)
        sent = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        assert sent["variables"]["first"] == 1


class TestPaginationBehaviour:
    """The reader does a SINGLE `items(first: N)` GraphQL request and does
    NOT follow `pageInfo.hasNextPage` / `endCursor`. `first` is clamped to
    GraphQL's 100-item ceiling, so boards larger than 100 items are
    truncated to the first page. These tests pin that real (capped, not
    paginated) contract so a future change to add cursor-following is a
    deliberate, test-visible decision — see GitHub Projects v2 pagination
    docs: https://docs.github.com/en/graphql/guides/using-pagination-in-the-graphql-api
    """

    def test_single_request_even_when_more_pages_exist(self, mocker, gh_env):
        body = _resp_body([_DRAFT_NODE])
        # A real connection would carry pageInfo signalling another page; the
        # reader ignores it and issues exactly one request.
        body["data"]["organization"]["projectV2"]["items"]["pageInfo"] = {
            "hasNextPage": True,
            "endCursor": "CURSOR_PAGE_2",
        }
        urlopen = mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            return_value=_FakeHTTPResponse(body),
        )
        cards = GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=200)
        assert urlopen.call_count == 1
        assert [c.key for c in cards] == ["draft:Spike: evaluate caching"]


class TestEmptyAndMalformed:
    def test_empty_connection_returns_empty(self, mocker, gh_env):
        mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            return_value=_FakeHTTPResponse(_resp_body([])),
        )
        assert GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=50) == []

    def test_non_dict_nodes_filtered_out(self, mocker, gh_env):
        # A null/garbage node in the connection must be dropped, not crash.
        mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            return_value=_FakeHTTPResponse(_resp_body([None, "junk", _DRAFT_NODE])),
        )
        cards = GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=50)
        assert [c.key for c in cards] == ["draft:Spike: evaluate caching"]

    def test_issue_without_repo_or_number_keys_as_draft(self, mocker, gh_env):
        node = {
            "id": "PVTI_x",
            "type": "ISSUE",
            "fieldValues": {"nodes": []},
            "content": {"__typename": "Issue", "title": "no repo", "body": ""},
        }
        mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            return_value=_FakeHTTPResponse(_resp_body([node])),
        )
        cards = GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=50)
        # repo+number both absent → key falls through to the "draft" sentinel.
        assert cards[0].key == "draft"
        assert cards[0].title == "no repo"

    def test_missing_projectv2_node_raises_not_found(self, mocker, gh_env):
        # Documented shape when the project number doesn't exist: the
        # `projectV2` field comes back null inside an otherwise-200 body.
        mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            return_value=_FakeHTTPResponse({"data": {"organization": {"projectV2": None}}}),
        )
        with pytest.raises(CliError, match="github project not found"):
            GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=50)

    def test_null_organization_raises_not_found(self, mocker, gh_env):
        mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            return_value=_FakeHTTPResponse({"data": {"organization": None}}),
        )
        with pytest.raises(CliError, match="github project not found"):
            GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=50)


class TestErrorEnvelopeAndHttp:
    def test_graphql_errors_envelope_raises(self, mocker, gh_env):
        # GraphQL returns HTTP 200 with an `errors` array for query-level
        # failures (e.g. NOT_FOUND, FORBIDDEN). Documented error envelope.
        payload = {
            "data": {"organization": None},
            "errors": [{"type": "NOT_FOUND", "message": "Could not resolve to an Organization with the login of 'bitspark-co'."}],
        }
        mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        )
        with pytest.raises(CliError, match="github graphql errors"):
            GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=50)

    @pytest.mark.parametrize("code", [401, 403, 404, 429, 500])
    def test_http_error_raises_with_status_and_detail(self, mocker, gh_env, code):
        detail = json.dumps({"message": "Bad credentials"})
        err = urllib.error.HTTPError(
            url="https://api.github.com/graphql",
            code=code,
            msg="error",
            hdrs=None,
            fp=io.BytesIO(detail.encode("utf-8")),
        )
        mocker.patch("briar.plan._boards.github_project.urllib.request.urlopen", side_effect=err)
        with pytest.raises(CliError) as exc:
            GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=50)
        assert f"github graphql HTTP {code}" in str(exc.value)
        assert "Bad credentials" in str(exc.value)

    def test_url_error_raises_request_failed(self, mocker, gh_env):
        mocker.patch(
            "briar.plan._boards.github_project.urllib.request.urlopen",
            side_effect=urllib.error.URLError("name resolution failed"),
        )
        with pytest.raises(CliError, match="github graphql request failed"):
            GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=50)

    def test_missing_token_raises_before_any_request(self, mocker, monkeypatch):
        # env_sandbox already strips GITHUB_TOKEN; assert no network attempt.
        urlopen = mocker.patch("briar.plan._boards.github_project.urllib.request.urlopen")
        with pytest.raises(CliError, match="GITHUB_TOKEN not set"):
            GithubProjectBoardReader().fetch(_ref(), company="acme", max_cards=50)
        urlopen.assert_not_called()


class TestFieldValues:
    """`_field_values` pulls the Status chip + label-ish chips out of the
    fieldValues blob. Schema varies per project, so it is best-effort."""

    def test_status_and_labels_extracted(self):
        node = {
            "nodes": [
                {"field": {"name": "Status"}, "name": "Todo"},
                {"field": {"name": "Labels"}, "name": "infra"},
                {"field": {"name": "Type"}, "name": "bug"},
            ]
        }
        status, labels = _field_values(node)
        assert status == "Todo"
        assert labels == ["infra", "bug"]

    def test_text_value_used_when_no_name(self):
        node = {"nodes": [{"field": {"name": "status"}, "text": "Blocked"}]}
        status, labels = _field_values(node)
        assert status == "Blocked"
        assert labels == []

    def test_non_dict_input_returns_empty(self):
        assert _field_values(None) == ("", [])
        assert _field_values([1, 2]) == ("", [])

    def test_rows_without_value_skipped(self):
        node = {"nodes": [{"field": {"name": "Status"}}, "junk", {"field": {"name": "Status"}, "name": "Done"}]}
        status, labels = _field_values(node)
        assert status == "Done"
        assert labels == []
