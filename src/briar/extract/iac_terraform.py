"""The Terraform of a repo: roots, backends, providers, modules and variable files.

Read through the provider API (`list_tree` then `read_file`, no clone), so it works for GitLab and
GitHub with the login `extract` already has. It answers the questions an agent has before it edits
infrastructure: where is this applied (the backend and the variable files), what does it use (the
providers and the modules, with their source and version), and which folders are only modules.

No secret value is ever written (decision E7): a variable file gives its variable NAMES only, and a
setting of a backend, a provider or a module goes through `SecretGuard`. What the small reader of
HCL cannot read (a map, a list, a heredoc) is shown as `(complex)`, not guessed."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from briar.extract._hcl import HclBlock, HclParser
from briar.extract._provider import RepositoryProvider
from briar.extract._secrets import SecretGuard
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section

CACHE_FOLDER = "/.terraform/"
VARIABLE_FILES, NAMES_A_ROOT, THE_REST = 0, 1, 2
ROOT_FILES = frozenset({"main.tf", "backend.tf", "providers.tf", "provider.tf", "versions.tf", "terraform.tf"})


@dataclass(frozen=True)
class TerraformFolder:
    """One folder with Terraform: its blocks and its variable files (file name -> variable names)."""

    path: str
    blocks: Tuple[HclBlock, ...]
    variable_files: Dict[str, List[str]]

    def backend(self) -> Optional[HclBlock]:
        found = self._in_terraform("backend")
        return found[0] if found else None

    def providers(self) -> List[str]:
        names = [block.labels[0] for block in self.blocks if block.kind == "provider" and block.labels]
        names += [name for block in self._in_terraform("required_providers") for name in block.attributes]
        return list(dict.fromkeys(names))

    def _in_terraform(self, kind: str) -> List[HclBlock]:
        """The child blocks of the `terraform { }` blocks that are of `kind`."""
        return [child for block in self.blocks if block.kind == "terraform" for child in block.children if child.kind == kind]

    def modules(self) -> List[HclBlock]:
        return [block for block in self.blocks if block.kind == "module"]

    def is_root(self) -> bool:
        """A root applies something: it has a backend, a provider, a call to a module or a variable file."""
        return self.backend() is not None or bool(self.providers()) or bool(self.modules()) or bool(self.variable_files)


class FolderText:
    """How a root is written in the section and in its data, through the secret guard."""

    def __init__(self, guard: SecretGuard) -> None:
        self._guard = guard

    def lines(self, folder: TerraformFolder) -> List[str]:
        out = [f"- `{folder.path}`"]
        backend = folder.backend()
        if backend is not None:
            out.append(f"  - backend {', '.join(backend.labels)}: {self._settings(backend.attributes)}")
        if folder.providers():
            out.append(f"  - providers: {', '.join(folder.providers())}")
        out += [f"  - module `{module.labels[0] if module.labels else '?'}`: {self._module(module)}" for module in folder.modules()]
        out += [f"  - variable file `{name}`: {self._variables(names)}" for name, names in folder.variable_files.items()]
        return out

    def data(self, folder: TerraformFolder) -> Dict[str, Any]:
        backend = folder.backend()
        return {
            "path": folder.path,
            "backend": None if backend is None else {"type": ", ".join(backend.labels), "settings": self._shown(backend.attributes)},
            "providers": folder.providers(),
            "modules": [{"name": ", ".join(module.labels), **self._shown(module.attributes, only=("source", "version"))} for module in folder.modules()],
            "variable_files": dict(folder.variable_files),
        }

    def _shown(self, attributes: Dict[str, str], only: Tuple[str, ...] = ()) -> Dict[str, str]:
        return {name: self._guard.shown(name, value) for name, value in attributes.items() if not only or name in only}

    def _settings(self, attributes: Dict[str, str]) -> str:
        return ", ".join(f"{name}={value}" for name, value in self._shown(attributes).items()) or "(no settings)"

    def _module(self, module: HclBlock) -> str:
        shown = self._shown(module.attributes, only=("source", "version"))
        version = f" (version {shown['version']})" if "version" in shown else ""
        return f"{shown.get('source', '(no source)')}{version}"

    def _variables(self, names: List[str]) -> str:
        return ", ".join(f"{name} (secret)" if self._guard.is_secret_name(name) else name for name in names)


@dataclass(frozen=True)
class ReadLimits:
    """How much of a repo is read: the most files, and where `list_tree` stops listing."""

    max_files: int
    tree_limit: int


@dataclass(frozen=True)
class RepoFacts:
    """What was found in a repo, ready to be written."""

    repo: str
    roots: List[TerraformFolder]
    module_folders: List[str]
    notes: List[str]


class TerraformRepo:
    """The Terraform of one repo, read through the provider."""

    def __init__(self, provider: RepositoryProvider, text: FolderText, limits: ReadLimits) -> None:
        self._provider = provider
        self._text = text
        self._limits = limits

    def section(self, repo: str) -> ExtractedSection:
        entries = self._provider.list_tree(repo)
        paths = sorted((entry.path for entry in entries if entry.is_file and self._wanted(entry.path)), key=self._priority)
        if not paths:
            return empty_section()
        read = paths[: self._limits.max_files]
        folders = self._folders(repo, read)
        facts = RepoFacts(
            repo,
            [folder for folder in folders if folder.is_root()],
            [folder.path for folder in folders if not folder.is_root()],
            self._notes(entries, read, paths),
        )
        return ExtractedSection(title=repo, body="\n".join(self._body(facts)), data=self._data(facts))

    def _priority(self, path: str) -> Tuple[int, str]:
        """Variable files first (they tell where it is applied and are what must not leak), then the
        files that usually name a root, then the rest; by path inside each group."""
        name = PurePosixPath(path).name
        group = VARIABLE_FILES if name.endswith(".tfvars") else NAMES_A_ROOT if name in ROOT_FILES else THE_REST
        return group, path

    def _wanted(self, path: str) -> bool:
        return path.endswith((".tf", ".tfvars")) and CACHE_FOLDER not in f"/{path}"

    def _folders(self, repo: str, paths: List[str]) -> List[TerraformFolder]:
        by_folder: Dict[str, List[str]] = {}
        for path in paths:
            by_folder.setdefault(str(PurePosixPath(path).parent), []).append(path)
        return [self._folder(repo, folder, files) for folder, files in by_folder.items()]

    def _folder(self, repo: str, folder: str, files: List[str]) -> TerraformFolder:
        parser = HclParser()
        blocks: List[HclBlock] = []
        variables: Dict[str, List[str]] = {}
        for path in files:
            text = self._provider.read_file(repo, path)
            name = PurePosixPath(path).name
            if path.endswith(".tfvars"):
                variables[name] = list(parser.attributes(text))
            else:
                blocks += parser.parse(text)
        return TerraformFolder(folder, tuple(blocks), variables)

    def _notes(self, entries: List[Any], read: List[str], paths: List[str]) -> List[str]:
        notes = [f"read {len(read)} of {len(paths)} Terraform files"] if len(read) < len(paths) else []
        cut = len(entries) >= self._limits.tree_limit
        return notes + ([f"the file list was cut at {self._limits.tree_limit} entries: later files are not seen"] if cut else [])

    def _body(self, facts: RepoFacts) -> List[str]:
        out = [f"Roots ({len(facts.roots)}):", *(line for root in facts.roots for line in self._text.lines(root))]
        modules = [f"`{path}`" for path in facts.module_folders]
        out += ["", f"Module folders: {', '.join(modules)}"] if modules else []
        return out + ([""] + facts.notes if facts.notes else [])

    def _data(self, facts: RepoFacts) -> Dict[str, Any]:
        return {"repo": facts.repo, "roots": [self._text.data(root) for root in facts.roots], "module_folders": facts.module_folders, "notes": facts.notes}


class ExtractIacTerraform(RepoBackedExtractor):
    name = "iac-terraform"
    heading = "Terraform"
    description = "Terraform roots, modules, backends, providers and variable files of repos (no secret value)"
    # `list_tree` stops here; a repo with more entries is read in part and the section says so.
    TREE_LIMIT = 5000
    # Placeholder, not measured on a real run: decision A1 fixes the real cap from data.
    DEFAULT_MAX_FILES = 400

    _availability_arg = "tf_repo"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument("--tf-repo", action="append", default=[], help="Repository slug whose Terraform to read. Repeatable.")
        parser.add_argument(
            "--tf-max-files", type=int, default=self.DEFAULT_MAX_FILES, help=f"Most Terraform files to read per repo (default: {self.DEFAULT_MAX_FILES})"
        )

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        limits = ReadLimits(max(int(args.tf_max_files), 1), self.TREE_LIMIT)
        reader = TerraformRepo(self._provider(args), FolderText(SecretGuard.standard()), limits)
        found = [section for section in (reader.section(repo) for repo in args.tf_repo) if not section.is_empty]
        if not found:
            return empty_section()
        return ExtractedSection(
            title=f"Terraform — {len(found)} repo(s)",
            body=(
                "Terraform of each repo: the roots (where it is applied: backend and variable files), what they use "
                "(providers, modules with source and version) and the folders that are only modules. Secret values are "
                "never written: a variable file lists names, and a name that looks like a secret is marked."
            ),
            subsections=found,
        )
