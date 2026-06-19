"""Shared scaffold context-filter flags fan out to every source kind,
with per-source flags overriding them."""

from __future__ import annotations

import argparse

import pytest

from briar.iac.scaffold._composer import add_common_arguments, attach_source_arguments
from briar.iac.scaffold.sources import SOURCE_TEMPLATES


def _parse(argv):
    parser = argparse.ArgumentParser(add_help=False)
    add_common_arguments(parser)
    attach_source_arguments(parser)
    return parser.parse_args(argv)


def test_shared_filter_applies_to_each_source():
    args = _parse(["--prefix", "p", "--authors-allow", "alice", "--assignees-block", "bot"])
    for kind in ("github", "bitbucket", "jira"):
        filters = SOURCE_TEMPLATES[kind]._user_filters(args)
        assert filters["authors_allow"] == ["alice"]
        assert filters["assignees_block"] == ["bot"]


def test_per_source_flag_overrides_shared():
    args = _parse(["--prefix", "p", "--authors-allow", "alice", "--jira-authors-allow", "carol"])
    jira = SOURCE_TEMPLATES["jira"]._user_filters(args)
    github = SOURCE_TEMPLATES["github"]._user_filters(args)
    assert jira["authors_allow"] == ["carol"]  # per-source wins
    assert github["authors_allow"] == ["alice"]  # shared still applies elsewhere


def test_no_filter_yields_empty_lists():
    args = _parse(["--prefix", "p"])
    github = SOURCE_TEMPLATES["github"]._user_filters(args)
    assert github == {
        "authors_allow": [],
        "authors_block": [],
        "assignees_allow": [],
        "assignees_block": [],
    }


@pytest.mark.parametrize("flag", ["--jira-authors-allow", "--github-assignees-block", "--bitbucket-authors-block"])
def test_per_source_flags_hidden_from_help(flag):
    parser = argparse.ArgumentParser(prog="briar scaffold implementation", add_help=False)
    add_common_arguments(parser)
    attach_source_arguments(parser)
    help_text = parser.format_help()
    assert flag not in help_text  # suppressed
    # but still parseable for back-compat
    args = parser.parse_args(["--prefix", "p", flag, "x"])
    assert "x" in vars(args)[flag.lstrip("-").replace("-", "_")]
