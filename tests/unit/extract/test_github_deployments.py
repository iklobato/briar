"""Behaviour tests for the github-deployments compose layer.

`ExtractGithubDeployments` renders three blocks per repo —
environments, recent deployments, recent CI runs — from the
provider's `list_environments` / `list_deployments` / `list_ci_runs`
verbs. The provider is mocked at those seams with a hand-rolled
`RepositoryProvider` subclass.

Fixture payloads model the documented GitHub REST shapes the provider
normalises into the `Environment` / `Deployment` / `CiRun` dataclasses:
  - environments:  https://docs.github.com/en/rest/deployments/environments#list-environments
                   (each env has `name`, `protection_rules[]`, `html_url`)
  - deployments:   https://docs.github.com/en/rest/deployments/deployments#list-deployments
                   (each has `id`, `environment`, `sha`, `creator.login`, `created_at`)
  - CI runs:       https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-repository
                   (each has `name`, `status`, `conclusion`, `head_branch`, `created_at`)
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract import EXTRACTORS
from briar.extract._provider import CiRun, Deployment, Environment, RepositoryProvider


class _DeployProvider(RepositoryProvider):
    kind = "fake"

    def __init__(self, *, envs=None, deploys=None, runs=None, company="", raises=None):
        self._company = company
        self._envs = envs or []
        self._deploys = deploys or []
        self._runs = runs or []
        self._raises = raises

    def is_available(self):
        return True

    def resolve_token(self):
        return "t"

    def clone_url(self, owner, repo):
        return ""

    def authed_clone_url(self, owner, repo, token):
        return ""

    def pr_creation_recipe(self, *, owner, repo, branch):
        return ""

    def list_pulls(self, repo, *, state, max_count):
        return []

    def read_file(self, repo, path):
        return ""

    def list_environments(self, repo):
        if self._raises:
            raise self._raises
        return list(self._envs)

    def list_deployments(self, repo, *, limit):
        self._deploy_limit = limit
        return list(self._deploys)

    def list_ci_runs(self, repo, *, limit):
        self._ci_limit = limit
        return list(self._runs)


def _args(repos=("o/r",)):
    return argparse.Namespace(deploy_repo=list(repos), provider="fake", company="")


def _run(provider, args):
    ext = EXTRACTORS["github-deployments"]
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_compose_renders_all_three_blocks_with_values():
    prov = _DeployProvider(
        envs=[Environment(name="production", protection_rule_count=2, url="https://gh/env/prod")],
        deploys=[
            Deployment(id="42", environment="production", sha="deadbeef", creator="octocat", created_at="2026-06-01T10:00:00Z"),
        ],
        runs=[
            CiRun(name="test", status="completed", conclusion="success", head_branch="main", created_at="2026-06-01T09:00:00Z"),
        ],
    )
    section = _run(prov, _args())

    assert section.title == "GitHub deployments — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"

    # data dict carries the normalised rows.
    assert repo.data["environments"] == [{"name": "production", "protection_rules": 2, "url": "https://gh/env/prod"}]
    assert repo.data["recent_deployments"][0]["sha"] == "deadbeef"
    assert repo.data["recent_ci_runs"][0]["conclusion"] == "success"

    # body must reflect the mocked values, not just headers.
    assert "**Environments:**" in repo.body
    assert "- production  protection_rules=2" in repo.body
    assert "production  sha=deadbeef  by=octocat" in repo.body
    assert "test  status=completed  conclusion=success  branch=main" in repo.body


@pytest.mark.unit
def test_empty_repo_renders_present_but_empty_body():
    # All three verbs empty → the section is still present (title=repo),
    # body is the explicit "_no deployments_" sentinel, NOT blank.
    section = _run(_DeployProvider(), _args())
    repo = section.subsections[0]
    assert repo.title == "o/r"
    assert repo.body == "_no deployments_"
    assert repo.data == {"environments": [], "recent_deployments": [], "recent_ci_runs": []}


@pytest.mark.unit
def test_only_environments_present_omits_other_headers():
    prov = _DeployProvider(envs=[Environment("staging", 0, "u")])
    repo = _run(prov, _args()).subsections[0]
    assert "**Environments:**" in repo.body
    assert "Recent deployments" not in repo.body
    assert "Recent CI runs" not in repo.body


@pytest.mark.unit
def test_deploy_and_ci_limits_passed_to_provider():
    prov = _DeployProvider(
        deploys=[Deployment("1", "e", "s", "c", "t")],
        runs=[CiRun("n", "s", "c", "b", "t")],
    )
    _run(prov, _args())
    assert prov._deploy_limit == 10
    assert prov._ci_limit == 5


@pytest.mark.unit
def test_multiple_repos_each_get_a_subsection():
    prov = _DeployProvider(envs=[Environment("prod", 1, "u")])
    section = _run(prov, _args(repos=("o/a", "o/b", "o/c")))
    assert section.title == "GitHub deployments — 3 repo(s)"
    assert [s.title for s in section.subsections] == ["o/a", "o/b", "o/c"]


@pytest.mark.unit
def test_no_repos_renders_zero_repo_header():
    # Defensive: with no --deploy-repo the top section still renders
    # with a 0-repo count and no subsections.
    section = _run(_DeployProvider(), _args(repos=()))
    assert section.title == "GitHub deployments — 0 repo(s)"
    assert section.subsections == []


@pytest.mark.unit
def test_provider_error_propagates():
    with pytest.raises(RuntimeError, match="403"):
        _run(_DeployProvider(raises=RuntimeError("403 forbidden")), _args())
