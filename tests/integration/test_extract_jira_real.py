"""End-to-end: `briar extract` driving the REAL atlassian-python-api Jira client
(requests transport) against a wire-level mock of Jira Cloud — for the
Jira-backed extractors active-tickets and ticket-archaeology.

No function-seam mock: the command, JiraTracker, JQL build, the POST to the v3
enhanced-search endpoint, response normalization into the Ticket model, the
extractor's aggregation, markdown rendering, and the file store all run. Each
test asserts (1) exit code, (2) the extractor's COMPUTED output read back off
disk, and (3) that the real client POSTed the documented JQL request body.

Jira Cloud REST v3 doc shapes (payloads modelled on these, not invented):
- enhanced search: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-jql-post
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def _run(cli, tmp_root, *flags):
    return cli(
        "extract",
        "--company",
        "acme",
        *flags,
        "--storage",
        "file",
        "--root",
        str(tmp_root / "knowledge"),
    )


def _disk_blob(tmp_root) -> str:
    return "\n".join(p.read_text() for p in (tmp_root / "knowledge").rglob("*") if p.is_file())


def _search_posts(received):
    return [r for r in received if r["path"] == "/rest/api/3/search/jql" and r["method"] == "POST"]


def _issue(key, summary, *, status, category, kind, reporter, assignee, labels, created, updated):
    # One issue object in the documented /search/jql response envelope.
    return {
        "key": key,
        "self": f"https://acme.atlassian.net/rest/api/3/issue/{key}",
        "fields": {
            "summary": summary,
            "status": {"name": status, "statusCategory": {"key": category}},
            "issuetype": {"name": kind},
            "priority": {"name": "Medium"},
            "reporter": {"displayName": reporter},
            "assignee": {"displayName": assignee} if assignee else None,
            "labels": labels,
            "created": created,
            "updated": updated,
        },
    }


# ─────────────────────────────── active-tickets ─────────────────────────────


def test_extract_active_tickets_real_jira(cli, jira_at, tmp_root) -> None:
    # Two OPEN tickets (statusCategory != Done). active-tickets renders one row
    # per ticket: key, title, status, reporter, assignee.
    jira_at.add(
        "POST",
        "/rest/api/3/search/jql",
        {
            "issues": [
                _issue(
                    "ENG-1",
                    "Login button misaligned",
                    status="In Progress",
                    category="indeterminate",
                    kind="Bug",
                    reporter="Alice",
                    assignee="Bob",
                    labels=["frontend"],
                    created="2026-04-01T09:00:00.000+0000",
                    updated="2026-04-02T09:00:00.000+0000",
                ),
                _issue(
                    "ENG-2",
                    "Add audit log",
                    status="To Do",
                    category="new",
                    kind="Story",
                    reporter="Carol",
                    assignee="",
                    labels=[],
                    created="2026-04-03T09:00:00.000+0000",
                    updated="2026-04-03T09:00:00.000+0000",
                ),
            ],
            "total": 2,
        },
    )

    result = _run(cli, tmp_root, "--include", "active-tickets", "--ticket-project", "ENG")

    assert result.code == 0, result.err
    posts = _search_posts(jira_at.received)
    assert posts, f"jira never called; received={[r['path'] for r in jira_at.received]}"
    # The tracker built an "open" JQL: statusCategory != Done for the project.
    sent = json.loads(posts[0]["body"])
    assert 'project = "ENG"' in sent["jql"]
    assert 'statusCategory != "Done"' in sent["jql"]
    assert posts[0]["headers"].get("Authorization", "").startswith("Basic ")

    blob = _disk_blob(tmp_root)
    assert "ENG — 2 open ticket(s)" in blob  # count computed by the extractor
    assert "ENG-1" in blob and "status=In Progress" in blob and "by=Alice" in blob and "to=Bob" in blob
    assert "ENG-2" in blob and "status=To Do" in blob and "by=Carol" in blob


# ──────────────────────────── ticket-archaeology ────────────────────────────


def test_extract_ticket_archaeology_real_jira(cli, jira_at, tmp_root) -> None:
    # Three CLOSED tickets. ticket-archaeology computes: closed count, median
    # time-to-close (created→updated), top reporters/assignees/labels/kinds.
    jira_at.add(
        "POST",
        "/rest/api/3/search/jql",
        {
            "issues": [
                # 24h close (Jan 01 09:00 → Jan 02 09:00)
                _issue(
                    "OPS-1",
                    "Disk full on db host",
                    status="Done",
                    category="done",
                    kind="Incident",
                    reporter="Alice",
                    assignee="Bob",
                    labels=["infra", "urgent"],
                    created="2026-01-01T09:00:00.000+0000",
                    updated="2026-01-02T09:00:00.000+0000",
                ),
                # 48h close
                _issue(
                    "OPS-2",
                    "Rotate TLS certs",
                    status="Done",
                    category="done",
                    kind="Task",
                    reporter="Alice",
                    assignee="Bob",
                    labels=["infra"],
                    created="2026-01-01T09:00:00.000+0000",
                    updated="2026-01-03T09:00:00.000+0000",
                ),
                # 12h close → median of [12,24,48] = 24h
                _issue(
                    "OPS-3",
                    "Bump base image",
                    status="Done",
                    category="done",
                    kind="Task",
                    reporter="Carol",
                    assignee="Dave",
                    labels=["infra"],
                    created="2026-01-01T09:00:00.000+0000",
                    updated="2026-01-01T21:00:00.000+0000",
                ),
            ],
            "total": 3,
        },
    )

    result = _run(cli, tmp_root, "--include", "ticket-archaeology", "--ticket-archaeology-project", "OPS")

    assert result.code == 0, result.err
    posts = _search_posts(jira_at.received)
    assert posts, f"jira never called; received={[r['path'] for r in jira_at.received]}"
    # The tracker built a "closed" JQL: statusCategory = Done.
    sent = json.loads(posts[0]["body"])
    assert 'project = "OPS"' in sent["jql"]
    assert 'statusCategory = "Done"' in sent["jql"]

    blob = _disk_blob(tmp_root)
    assert "closed ticket sample: **3**" in blob  # count computed
    assert "median time-to-close: **24.0h**" in blob  # median([12,24,48]) computed
    # Aggregations: Alice reports 2, infra label on all 3, Task kind twice.
    assert "top reporters: Alice(2)" in blob
    assert "infra(3)" in blob
    assert "Task(2)" in blob


# ───────────────────────────── unhappy paths ────────────────────────────────


def test_extract_active_tickets_jira_empty_yields_nonzero(cli, jira_at, tmp_root) -> None:
    # No open tickets → the only enabled extractor produces an empty section and
    # the run exits non-zero ("nothing extracted") instead of writing a blob.
    jira_at.add("POST", "/rest/api/3/search/jql", {"issues": [], "total": 0})

    result = _run(cli, tmp_root, "--include", "ticket-archaeology", "--ticket-archaeology-project", "OPS")

    assert result.code != 0
    assert _search_posts(jira_at.received), "jira should still have been queried"


def test_extract_active_tickets_jira_401_is_swallowed_to_empty(cli, jira_at, tmp_root) -> None:
    # 401 from Jira: JiraTracker.list_tickets is @swallow_errors(default=[]), so
    # the tracker returns no tickets rather than crashing. active-tickets ALWAYS
    # emits a per-project section (unlike ticket-archaeology), so the run still
    # exits 0 and writes a "0 open ticket(s)" section — but crucially the auth
    # error is swallowed: no HTTPError traceback reaches the user (stderr empty).
    jira_at.add("POST", "/rest/api/3/search/jql", {"errorMessages": ["Unauthorized"], "errors": {}}, status=401)

    result = _run(cli, tmp_root, "--include", "active-tickets", "--ticket-project", "ENG")

    assert result.code == 0, result.err
    assert result.err == ""  # the 401 must NOT surface as an uncaught crash to the user
    assert _search_posts(jira_at.received), "jira should have been queried before the 401"
    blob = _disk_blob(tmp_root)
    assert "ENG — 0 open ticket(s)" in blob  # degrades gracefully to empty, not bogus data
    assert "_no open tickets_" in blob
