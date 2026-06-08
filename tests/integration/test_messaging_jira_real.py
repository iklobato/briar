"""End-to-end: the REAL Jira writers (atlassian-python-api over `requests`)
against a wire-level mock of Jira Cloud, so the with_ai_prefix body, the
transition-id resolution, the real client POST, and the SendResult mapping
all run.

The writer's client uses the library default API version (v2), so the REST
paths are under /rest/api/2 (the JiraTracker reads use v3 — different surface).

Jira comments:    POST /rest/api/2/issue/{key}/comment
  https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/
        #api-rest-api-3-issue-issueidorkey-comment-post
  201 Created; the response body carries the new comment `id` (string).

Jira transitions: GET  /rest/api/2/issue/{key}/transitions  (resolve id by name)
                  POST /rest/api/2/issue/{key}/transitions  (apply it)
  https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/
        #api-rest-api-3-issue-issueidorkey-transitions-post
  204 No Content on success (no body).

REGRESSION GUARD: this is the tier that caught the jira-transition bug — the
real client's set_issue_status has no `comment` kwarg, so the old code raised
TypeError (swallowed -> silent ok=False; the transition never happened). A
resolution note now rides the transition POST via the `update` field. These
tests assert the posted payload, so a reintroduction of the bug would FAIL here.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def test_jira_comment_real_posts_body(jira_at) -> None:
    from briar.messaging.jira_comment import JiraCommentWriter

    jira_at.add(
        "POST",
        "/rest/api/2/issue/ENG-1/comment",
        {"id": "10100", "body": "[AI] looks good", "self": "https://acme.atlassian.net/rest/api/2/issue/10001/comment/10100"},
        status=201,
    )

    result = JiraCommentWriter(company="acme").send(target="ENG-1", body="looks good")

    assert result.ok is True
    assert result.ref == "10100"  # the real comment id from the 201 body

    posts = [r for r in jira_at.received if r["method"] == "POST" and r["path"] == "/rest/api/2/issue/ENG-1/comment"]
    assert posts, f"never posted; received={[(r['method'], r['path']) for r in jira_at.received]}"
    payload = json.loads(posts[0]["body"])
    assert payload == {"body": "[AI] looks good"}  # with_ai_prefix applied


def test_jira_comment_real_404_is_swallowed(jira_at) -> None:
    from briar.messaging.jira_comment import JiraCommentWriter

    # Jira error envelope: {"errorMessages": [...], "errors": {...}}.
    jira_at.add(
        "POST",
        "/rest/api/2/issue/NOPE-9/comment",
        {"errorMessages": ["Issue does not exist or you do not have permission to see it."], "errors": {}},
        status=404,
    )

    result = JiraCommentWriter(company="acme").send(target="NOPE-9", body="hi")

    assert result.ok is False
    assert result.detail == "exception"


def test_jira_transition_real_resolves_id_and_posts(jira_at) -> None:
    from briar.messaging.jira_transition import JiraTransitionWriter

    # The library GETs the available transitions, matches by destination
    # status name, then POSTs {"transition": {"id": <int>}, ...}.
    jira_at.add(
        "GET",
        "/rest/api/2/issue/ENG-2/transitions",
        {
            "transitions": [
                {"id": "11", "name": "Start Progress", "to": {"name": "In Progress"}},
                {"id": "31", "name": "Resolve", "to": {"name": "Done"}},
            ]
        },
    )
    jira_at.add("POST", "/rest/api/2/issue/ENG-2/transitions", {}, status=204)

    result = JiraTransitionWriter(company="acme").send(target="ENG-2", body="resolving", status="Done")

    assert result.ok is True
    assert result.ref == "ENG-2→Done"  # 204 -> client returns None -> writer's friendly ref

    posts = [r for r in jira_at.received if r["method"] == "POST" and r["path"] == "/rest/api/2/issue/ENG-2/transitions"]
    assert posts, f"never POSTed the transition; received={[(r['method'], r['path']) for r in jira_at.received]}"
    payload = json.loads(posts[0]["body"])
    # Chose the transition whose `to` name == "Done" (id 31), not the first one.
    assert payload["transition"] == {"id": 31}
    # The resolution comment rides the transition POST via `update` (the fix
    # for the set_issue_status(comment=...) TypeError bug), [AI]-prefixed.
    assert payload["update"] == {"comment": [{"add": {"body": "[AI] resolving"}}]}


def test_jira_transition_real_empty_body_omits_comment(jira_at) -> None:
    from briar.messaging.jira_transition import JiraTransitionWriter

    jira_at.add(
        "GET",
        "/rest/api/2/issue/ENG-3/transitions",
        {"transitions": [{"id": "31", "name": "Resolve", "to": {"name": "Done"}}]},
    )
    jira_at.add("POST", "/rest/api/2/issue/ENG-3/transitions", {}, status=204)

    result = JiraTransitionWriter(company="acme").send(target="ENG-3", body="", status="Done")

    assert result.ok is True
    posts = [r for r in jira_at.received if r["method"] == "POST" and r["path"].endswith("/transitions")]
    payload = json.loads(posts[0]["body"])
    assert payload["transition"] == {"id": 31}
    # No body -> no `update` block at all.
    assert "update" not in payload


def test_jira_transition_real_status_from_config_default(jira_at) -> None:
    """status comes from the binding config when no extras.status is given."""
    from briar.messaging.jira_transition import JiraTransitionWriter

    jira_at.add(
        "GET",
        "/rest/api/2/issue/ENG-4/transitions",
        {
            "transitions": [
                {"id": "21", "name": "Review", "to": {"name": "In Review"}},
                {"id": "31", "name": "Resolve", "to": {"name": "Done"}},
            ]
        },
    )
    jira_at.add("POST", "/rest/api/2/issue/ENG-4/transitions", {}, status=204)

    result = JiraTransitionWriter(company="acme", config={"status": "In Review"}).send(target="ENG-4", body="")

    assert result.ok is True
    assert result.ref == "ENG-4→In Review"
    payload = json.loads([r for r in jira_at.received if r["method"] == "POST"][0]["body"])
    assert payload["transition"] == {"id": 21}  # matched "In Review", not "Done"


def test_jira_transition_real_404_is_swallowed(jira_at) -> None:
    """A 404 on the transitions GET (bad key) raises -> swallowed ok=False,
    and the writer never POSTs."""
    from briar.messaging.jira_transition import JiraTransitionWriter

    jira_at.add(
        "GET",
        "/rest/api/2/issue/NOPE-9/transitions",
        {"errorMessages": ["Issue does not exist or you do not have permission to see it."], "errors": {}},
        status=404,
    )

    result = JiraTransitionWriter(company="acme").send(target="NOPE-9", body="x", status="Done")

    assert result.ok is False
    assert result.detail == "exception"
    assert not [r for r in jira_at.received if r["method"] == "POST"]
