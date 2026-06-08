"""End-to-end: the REAL atlassian-python-api Jira client (requests transport)
talks to a wire-level mock of Jira Cloud, so the tracker's JQL build, POST, and
response normalization all run.

Jira Cloud enhanced search: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_jira_tracker_real_client_lists_tickets(jira_at) -> None:
    from briar.extract._trackers import make_tracker

    # Documented /search/jql response envelope.
    jira_at.add(
        "POST",
        "/rest/api/3/search/jql",
        {
            "issues": [
                {
                    "key": "ENG-42",
                    "self": "https://acme.atlassian.net/rest/api/3/issue/10042",
                    "fields": {
                        "summary": "Login 500s under load",
                        "status": {"name": "Done", "statusCategory": {"key": "done"}},
                        "issuetype": {"name": "Bug"},
                        "priority": {"name": "High"},
                        "reporter": {"displayName": "Alice"},
                        "assignee": {"displayName": "Bob"},
                        "labels": ["backend"],
                        "created": "2026-01-01T09:00:00.000+0000",
                        "updated": "2026-01-02T10:00:00.000+0000",
                    },
                }
            ],
            "total": 1,
        },
    )

    tracker = make_tracker("jira", company="acme")
    tickets = tracker.list_tickets("ENG", state="closed", max_count=10)

    # Real client really POSTed the JQL to the v3 enhanced-search endpoint.
    posts = [r for r in jira_at.received if r["path"] == "/rest/api/3/search/jql"]
    assert posts, f"jira never called; received={[r['path'] for r in jira_at.received]}"
    # The envelope was normalized into the tracker's Ticket model.
    assert len(tickets) == 1
    assert tickets[0].key == "ENG-42"
    assert tickets[0].title == "Login 500s under load"
    assert tickets[0].reporter == "Alice"
    assert tickets[0].assignee == "Bob"
