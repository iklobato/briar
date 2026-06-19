"""`briar init` — generate a starter `.briar.toml`.

Pre-fills the repo owner/name from the git `origin` remote when present,
so a new project gets a working config without hand-authoring TOML."""

from __future__ import annotations

import argparse
import sys
from importlib.resources import files
from pathlib import Path

from briar.commands.base import Command
from briar.errors import CliError
from briar.storage import KNOWLEDGE_STORE_NAMES

_TEMPLATE = "briar.init_templates"


class CommandInit(Command):
    name = "init"
    help = "Generate a starter .briar.toml (repo inferred from git)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--company", default="", help="Company key to write into the config")
        parser.add_argument(
            "--store",
            choices=list(KNOWLEDGE_STORE_NAMES),
            default="file",
            help="Default knowledge store backend (default: file)",
        )
        parser.add_argument("--owner", default="", help="Repo owner (default: inferred from git origin)")
        parser.add_argument("--repo", default="", help="Repo name (default: inferred from git origin)")
        parser.add_argument("--path", default=".briar.toml", help="Output path (default: ./.briar.toml)")
        parser.add_argument("--force", action="store_true", help="Overwrite an existing file")

    def run(self, args: argparse.Namespace) -> int:
        path = Path(args.path)
        if path.exists() and not args.force:
            raise CliError(f"{path} already exists — pass --force to overwrite")
        owner, repo = self._resolve_repo(args.owner, args.repo)
        content = self._render(company=args.company, store=args.store, owner=owner, repo=repo)
        path.write_text(content)
        print(f"wrote {path}", file=sys.stderr)
        print(content, end="")
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

    @staticmethod
    def _render(*, company: str, store: str, owner: str, repo: str) -> str:
        template = files(_TEMPLATE).joinpath("briar_toml.tmpl").read_text(encoding="utf-8")
        markers = {"COMPANY": company, "STORE": store, "OWNER": owner, "REPO": repo}
        for key, value in markers.items():
            template = template.replace(f"@@{key}@@", value)
        return template
