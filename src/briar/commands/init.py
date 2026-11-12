"""`briar init`: generate a starter `.briar.toml` (for `briar extract`) and an example
runbook `runbooks/<company>.yaml` (for `briar serve`; spec 003 part 4, X3).

Pre-fills the repo owner/name from the git `origin` remote when present, so a new
project gets a working config without hand-authoring TOML."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Mapping

import typer

from briar.commands.base import Command, CommandOptions, Route
from briar.errors import CliError

_TEMPLATE = "briar.init_templates"


class CommandInit(Command):
    name = "init"
    help = "Generate a starter .briar.toml and an example runbook (repo inferred from git)."
    # The company of the example runbook when `--company` is not given, and its repo
    # when git has no `origin`.
    EXAMPLE_COMPANY = "example"
    EXAMPLE_REPO = "your-org/your-repo"

    def run(self, args: argparse.Namespace) -> int:
        path = Path(args.path)
        company = args.company or self.EXAMPLE_COMPANY
        runbook = path.parent / "runbooks" / f"{company}.yaml"
        taken = [str(target) for target in (path, runbook) if target.exists()]
        if taken and not args.force:
            raise CliError(f"{' and '.join(taken)} already exists: pass --force to overwrite")
        owner, repo = self._resolve_repo(args.owner, args.repo)
        content = self._render("briar_toml.tmpl", {"COMPANY": args.company, "OWNER": owner, "REPO": repo})
        path.write_text(content)
        slug = f"{owner}/{repo}" if owner and repo else self.EXAMPLE_REPO
        runbook.parent.mkdir(parents=True, exist_ok=True)
        runbook.write_text(self._render("runbook.yaml.tmpl", {"COMPANY": company, "PREFIX": company.upper().replace("-", "_"), "SLUG": slug}))
        sys.stderr.write(f"wrote {path} and {runbook}\n")
        sys.stdout.write(content)
        return 0

    @staticmethod
    def _resolve_repo(owner: str, repo: str) -> tuple[str, str]:
        """Fill blanks from the git origin remote; explicit flags win."""
        if owner and repo:
            return owner, repo
        from briar.infer import git_remote_slug

        slug = git_remote_slug()
        if slug is None:
            return owner, repo
        return owner or slug[0], repo or slug[1]

    def _render(self, template: str, markers: Mapping[str, str]) -> str:
        text = files(_TEMPLATE).joinpath(template).read_text(encoding="utf-8")
        for key, value in markers.items():
            text = text.replace(f"@@{key}@@", value)
        return text


@dataclass(frozen=True)
class InitOptions(CommandOptions):
    ROUTE = Route(CommandInit.name)

    company: str = typer.Option("", "--company", help="Company key to write into the config")
    owner: str = typer.Option("", "--owner", help="Repo owner (default: inferred from git origin)")
    repo: str = typer.Option("", "--repo", help="Repo name (default: inferred from git origin)")
    path: str = typer.Option(".briar.toml", "--path", help="Output path (default: ./.briar.toml)")
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file")
