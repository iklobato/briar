"""`briar serve` on GitLab (spec 008 PR 3b, G1, G2, G9, G10): the host and the style the
company code host gives. No network."""

from __future__ import annotations

from pathlib import Path

from briar.extract._providers.github import GithubProvider
from briar.extract._providers.gitlab_host import GitlabHost
from briar.iac.runbook.models import CodeHostKind
from briar.serve.agent import BashGuard
from briar.serve.git import GitIdentity, Remote
from briar.serve.host import GitlabStyle
from briar.serve.work import WorkSources
from tests.unit.extract.gitlab_fakes import FakeGitlabApi
from tests.unit.fake_logins import FakeLogins

FAKE_TOKEN = "PLACEHOLDER-not-a-secret"
MR = 7


class Logins:
    def gitlab(self) -> FakeLogins:
        return FakeLogins({"gitlab": {"url": "https://gitlab.acme.test"}}, {"BRIAR_ACME_GITLAB_TOKEN": FAKE_TOKEN})


class TestSources:
    def test_github_by_default(self) -> None:
        assert isinstance(WorkSources(FakeLogins().logins()).code_host(), GithubProvider)

    def test_gitlab_when_the_company_says_so(self) -> None:
        sources = WorkSources(Logins().gitlab().logins(), CodeHostKind.GITLAB)

        assert isinstance(sources.code_host(), GitlabHost)
        assert sources.style().pull_url("acme/platform/app", MR) == "https://gitlab.acme.test/acme/platform/app/-/merge_requests/7"

    def test_a_gitlab_clone_uses_the_login_url_and_oauth2(self) -> None:
        remote = WorkSources(Logins().gitlab().logins(), CodeHostKind.GITLAB).style().remote("acme/platform/app")

        assert (remote, remote.address()) == (Remote("acme/platform/app", "https://gitlab.acme.test", "oauth2"), "https://gitlab.acme.test/acme/platform/app.git")


class TestIdentity:
    def test_gitlab_commits_carry_the_commit_email_of_the_profile(self) -> None:
        """G9: GitLab links the commit to the user by this email."""
        host = GitlabHost(Logins().gitlab().logins().gitlab(), FakeGitlabApi({"/user": {"username": "octo", "commit_email": "octo@acme.test"}}))

        identity = GitlabStyle("https://gitlab.acme.test", host).identity()

        assert identity.args() == ("-c", "user.name=octo", "-c", "user.email=octo@acme.test")

    def test_github_commits_keep_the_noreply_email(self) -> None:
        assert GitIdentity("octo").args() == ("-c", "user.name=octo", "-c", "user.email=octo@users.noreply.github.com")


class TestAgentBash:
    def test_glab_is_blocked_as_gh_is(self, tmp_path: Path) -> None:
        """G10: every write to GitLab goes through briar."""
        guard = BashGuard(tmp_path, [], [tmp_path])

        refused = [bool(guard.refusal("Bash", {"command": command})) for command in ("glab mr create", "gh pr create", "git status")]

        assert refused == [True, True, False]
