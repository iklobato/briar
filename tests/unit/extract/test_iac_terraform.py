"""`iac-terraform`: the Terraform of a repo, read through the provider API, with no secret value written.

The provider is a hand-rolled `RepositoryProvider` over a dict of files, the same pattern as the other
extractor tests. The fake secret values must never reach the section (decision E7, acceptance E17b)."""

from __future__ import annotations

import argparse
from typing import Dict, List

import pytest

from briar.extract._provider import PullRequest, RepositoryProvider, TreeEntry
from briar.extract.base import ExtractedSection
from briar.extract.iac_terraform import ExtractIacTerraform

FAKE_PASSWORD = "FAKE-not-a-real-secret"
FAKE_BACKEND_KEY = "FAKE-backend-access-key"
MAX_FILES = 400
TERRAFORM_FILES = 3

PROD_MAIN = f"""
terraform {{
  backend "azurerm" {{
    storage_account_name = "stgprod"
    container_name       = "tfstate"
    access_key           = "{FAKE_BACKEND_KEY}"
  }}
}}

provider "azurerm" {{
  features {{}}
}}

module "network" {{
  source  = "git::https://user:{FAKE_PASSWORD}@host.example/mods.git//network?ref=v1.2.0"
  version = "1.2.0"
}}

module "db" {{
  source = "../../modules/db"
}}
"""

PROD_TFVARS = f'location = "eastus"\nadmin_password = "{FAKE_PASSWORD}"\ntags = {{\n  a = "b"\n}}\n'


class Repo:
    """The files of a repo of infrastructure."""

    def files(self) -> Dict[str, str]:
        return {
            "infra/envs/prod/main.tf": PROD_MAIN,
            "infra/envs/prod/prod.tfvars": PROD_TFVARS,
            "infra/modules/db/main.tf": 'variable "name" {}\nresource "azurerm_thing" "x" {\n  name = var.name\n}\n',
            "infra/.terraform/providers/skip.tf": 'provider "skipped" {}\n',
            "README.md": "hello",
        }


class FilesProvider(RepositoryProvider):
    """A provider that lists and reads a dict of files; every other verb is inert."""

    kind = "fake"

    def __init__(self, files: Dict[str, str]) -> None:
        self._files = files
        self.reads: List[str] = []

    def is_available(self) -> bool:
        return True

    def resolve_token(self) -> str:
        return "fake-token"

    def clone_url(self, owner: str, repo: str) -> str:
        return f"https://fake/{owner}/{repo}.git"

    def authed_clone_url(self, owner: str, repo: str, token: str) -> str:
        return f"https://x:{token}@fake/{owner}/{repo}.git"

    def list_pulls(self, repo: str, *, state: str, max_count: int) -> List[PullRequest]:
        return []

    def read_file(self, repo: str, path: str) -> str:
        self.reads.append(path)
        return self._files.get(path, "")

    def list_tree(self, repo: str, *, max_count: int = ExtractIacTerraform.TREE_LIMIT) -> List[TreeEntry]:
        return [TreeEntry(path) for path in self._files]


class Terraform:
    """One extraction of the Terraform of a provider, with the namespace a real run builds."""

    def __init__(self, provider: FilesProvider) -> None:
        self.provider = provider

    def section(self, max_files: int = MAX_FILES) -> ExtractedSection:
        args = argparse.Namespace(tf_repo=["o/infra"], tf_max_files=max_files, provider="fake", company="")
        extractor = ExtractIacTerraform()
        extractor._provider = lambda _args: self.provider  # type: ignore[method-assign]
        return extractor.extract(args)

    def every_text(self, section: ExtractedSection) -> str:
        """All the text a section carries: titles, bodies, data, and the same of its subsections."""
        own = f"{section.title}\n{section.body}\n{section.data}"
        return "\n".join([own, *(self.every_text(child) for child in section.subsections)])


@pytest.fixture
def terraform() -> Terraform:
    return Terraform(FilesProvider(Repo().files()))


class TestTerraformRoots:
    def test_a_repo_with_terraform_is_one_subsection_named_after_it(self, terraform: Terraform) -> None:
        section = terraform.section()

        assert (section.title, [child.title for child in section.subsections]) == ("Terraform — 1 repo(s)", ["o/infra"])

    def test_a_root_shows_its_backend_providers_modules_and_variable_files(self, terraform: Terraform) -> None:
        body = terraform.section().subsections[0].body

        assert "`infra/envs/prod`" in body
        assert "backend azurerm: storage_account_name=stgprod, container_name=tfstate, access_key=(set)" in body
        assert "providers: azurerm" in body
        assert "module `network`: git::https://host.example/mods.git//network?ref=v1.2.0 (version 1.2.0)" in body
        assert "module `db`: ../../modules/db" in body
        assert "variable file `prod.tfvars`: location, admin_password (secret), tags" in body

    def test_a_folder_with_no_backend_provider_or_module_call_is_a_module_folder(self, terraform: Terraform) -> None:
        roots, folders = terraform.section().subsections[0].body.split("Module folders:")

        assert "`infra/modules/db`" in folders
        assert "`infra/modules/db`" not in roots

    def test_a_terraform_cache_folder_is_not_read(self, terraform: Terraform) -> None:
        terraform.section()

        assert "infra/.terraform/providers/skip.tf" not in terraform.provider.reads


class TestNoSecretValue:
    def test_no_secret_value_reaches_the_section_in_any_field(self, terraform: Terraform) -> None:
        text = terraform.every_text(terraform.section())

        assert FAKE_PASSWORD not in text
        assert FAKE_BACKEND_KEY not in text

    def test_the_data_of_the_section_has_the_names_and_never_the_values(self, terraform: Terraform) -> None:
        roots = terraform.section().subsections[0].data["roots"]

        assert roots[0]["variable_files"]["prod.tfvars"] == ["location", "admin_password", "tags"]


class TestLimits:
    def test_a_repo_with_no_terraform_gives_no_section(self) -> None:
        section = Terraform(FilesProvider({"README.md": "x"})).section()

        assert section.is_empty

    def test_a_tree_cut_at_the_limit_says_so(self) -> None:
        files = {f"docs/{index}.md": "" for index in range(ExtractIacTerraform.TREE_LIMIT - 1)}
        files["main.tf"] = 'provider "x" {}\n'

        body = Terraform(FilesProvider(files)).section().subsections[0].body

        assert "the file list was cut" in body

    def test_only_the_max_files_are_read_and_the_rest_is_counted(self) -> None:
        provider = FilesProvider(Repo().files())

        body = Terraform(provider).section(max_files=1).subsections[0].body

        assert len(provider.reads) == 1
        assert f"read 1 of {TERRAFORM_FILES} Terraform files" in body

    def test_the_variable_files_are_read_first_because_they_tell_where_it_is_applied(self) -> None:
        provider = FilesProvider(Repo().files())

        body = Terraform(provider).section(max_files=1).subsections[0].body

        assert provider.reads == ["infra/envs/prod/prod.tfvars"]
        assert "- `infra/envs/prod`" in body
        assert "variable file `prod.tfvars`: location, admin_password (secret), tags" in body

    def test_the_files_that_name_a_root_are_read_before_the_others(self) -> None:
        files = {"a/other.tf": 'resource "x" "y" {}\n', "z/backend.tf": 'terraform {\n  backend "s3" {}\n}\n'}
        provider = FilesProvider(files)

        Terraform(provider).section(max_files=1)

        assert provider.reads == ["z/backend.tf"]

    def test_a_run_with_no_repo_is_not_available(self) -> None:
        assert ExtractIacTerraform().is_available(argparse.Namespace(tf_repo=[], provider="fake", company="")) is False
