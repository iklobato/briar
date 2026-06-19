"""Legacy per-extractor / per-source flags map to their canonical
replacement for the deprecation notice; canonical + unique flags don't."""

from __future__ import annotations

from briar.extract.canonical import legacy_flag_suggestions


def test_legacy_extractor_flags_suggest_canonical():
    argv = ["extract", "--company", "acme", "--reviewer-repo", "a/b", "--risk-since-days", "90", "--pr-max=50"]
    assert legacy_flag_suggestions(argv) == {
        "--reviewer-repo": "--repo",
        "--risk-since-days": "--since-days",
        "--pr-max": "--max",
    }


def test_legacy_scaffold_source_filters_suggest_canonical():
    argv = ["scaffold", "implementation", "--jira-authors-allow", "alice", "--github-assignees-block", "bot"]
    assert legacy_flag_suggestions(argv) == {
        "--jira-authors-allow": "--authors-allow",
        "--github-assignees-block": "--assignees-block",
    }


def test_canonical_flags_are_not_flagged():
    argv = ["extract", "--repo", "a/b", "--since-days", "30", "--top-n", "5", "--authors-allow", "x"]
    assert legacy_flag_suggestions(argv) == {}


def test_unique_flags_are_not_flagged():
    argv = ["extract", "--gov-branch", "main", "--stale-days", "7", "--aws-extract-region", "us-east-1", "--meeting-attendee-allow", "x@y.com"]
    assert legacy_flag_suggestions(argv) == {}


def test_unrelated_flags_are_not_flagged():
    argv = ["plan", "build", "--max-cards", "50", "--default-branch", "main"]
    assert legacy_flag_suggestions(argv) == {}


def test_tracker_project_flags_are_not_deprecated():
    """--ticket-project / --ticket-archaeology-project are the documented
    way to name a tracker project (and the divergent-case override), not
    deprecated aliases — they must not be nagged about."""
    argv = [
        "extract",
        "--include",
        "active-tickets",
        "--ticket-project",
        "ACME",
        "--ticket-archaeology-project",
        "PLAT",
    ]
    assert legacy_flag_suggestions(argv) == {}
