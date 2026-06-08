"""Board-reader registry — URL → reader resolution + error surface.

`tests/test_plan.py` already pins that both kinds register and that an
unknown URL raises. These add the resolution-correctness assertions:
the *right* concrete reader is returned per URL family, and the
"unrecognised" error names the registered kinds so an operator can see
what was tried.
"""

from __future__ import annotations

import pytest

from briar.errors import CliError
from briar.plan._boards import BOARD_READERS, BoardReaderRegistry, resolve_board
from briar.plan._boards.github_project import GithubProjectBoardReader
from briar.plan._boards.jira_board import JiraBoardReader


class TestResolve:
    def test_jira_full_url_resolves_to_jira_reader(self):
        reader = resolve_board("https://acme.atlassian.net/jira/software/projects/ENG/boards/12")
        assert isinstance(reader, JiraBoardReader)

    def test_jira_short_form_resolves_to_jira_reader(self):
        assert isinstance(resolve_board("jira:ENG"), JiraBoardReader)

    def test_github_org_url_resolves_to_github_reader(self):
        reader = resolve_board("https://github.com/orgs/foo/projects/3")
        assert isinstance(reader, GithubProjectBoardReader)

    def test_github_user_url_resolves_to_github_reader(self):
        reader = resolve_board("https://github.com/users/octocat/projects/3")
        assert isinstance(reader, GithubProjectBoardReader)

    def test_unknown_url_error_lists_registered_kinds(self):
        with pytest.raises(CliError) as exc:
            resolve_board("https://example.com/nope")
        msg = str(exc.value)
        assert "github-project" in msg
        assert "jira" in msg

    def test_kinds_exposes_registered_readers(self):
        kinds = BoardReaderRegistry.kinds()
        assert set(kinds) == {"jira", "github-project"}

    def test_registry_keyed_by_kind_attr(self):
        assert isinstance(BOARD_READERS["jira"], JiraBoardReader)
        assert isinstance(BOARD_READERS["github-project"], GithubProjectBoardReader)
