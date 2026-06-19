"""`briar extract` — run one or more knowledge extractors, write the
result to a local markdown file."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from briar.commands.base import Command
from briar.errors import CliError
from briar.extract import EXTRACTORS
from briar.extract.base import ExtractedSection
from briar.extract.canonical import AdvancedHelpAction, apply_canonical, hide_canonicalised_flags, register_canonical_flags
from briar.extract.claude_md import ClaudeMdMerger
from briar.extract.composer import render_json, render_markdown
from briar.storage import KNOWLEDGE_STORE_NAMES, make_store

_CLAUDE_MD_DETAIL_ROOT = Path(".briar/knowledge")


def _company_slug(company: str) -> str:
    """Filesystem-safe stem for the detail file. Non-alphanumeric runs
    collapse to a single dash so ``Acme Inc.`` and ``acme-inc`` don't
    fight over the same path."""
    slug = "".join(char if char.isalnum() else "-" for char in company.lower())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "knowledge"


class CommandExtract(Command):
    name = "extract"
    help = "Mine the live state of GitHub / AWS / etc. into a markdown " "knowledge blob written to local disk."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--company",
            required=True,
            help="Company name (drives the markdown title + blob name)",
        )
        parser.add_argument(
            "--include",
            action="append",
            default=[],
            choices=sorted(EXTRACTORS.keys()),
            help="Which extractor(s) to run (repeatable; default: all available)",
        )
        parser.add_argument(
            "--storage",
            default="file",
            choices=list(KNOWLEDGE_STORE_NAMES),
            help="Where to write the result (default: file)",
        )
        parser.add_argument("--blob-name", default="", help="Storage blob name (default: knowledge:<company>)")
        parser.add_argument("--root", default="./knowledge", help="Local file root (only used when --storage=file)")
        parser.add_argument("--out-json", default="", help="Parallel JSON output path (empty = skip)")
        parser.add_argument(
            "--merge-claude-md",
            action="store_true",
            help="Also merge a knowledge index into CLAUDE.md so Claude Code " "sessions can read the full detail on demand",
        )
        parser.add_argument(
            "--claude-md-path",
            default="./CLAUDE.md",
            help="CLAUDE.md to merge the knowledge index into (only used with --merge-claude-md)",
        )
        parser.add_argument(
            "--advanced-help",
            action=AdvancedHelpAction,
            help="Show the full per-extractor override flags and exit.",
        )
        # Canonical flags first (the headline surface), then the
        # per-extractor private flags. The private flags stay registered
        # for back-compat + the rare same-invocation-divergent case, but
        # `hide_canonicalised_flags` suppresses the redundant ones from
        # `-h` so the default help shows just the canonical + core knobs.
        register_canonical_flags(parser)
        for ext in EXTRACTORS.values():
            ext.add_arguments(parser)
        hide_canonicalised_flags(parser)

    def run(self, args: argparse.Namespace) -> int:
        selected = args.include or list(EXTRACTORS.keys())

        sections: List[ExtractedSection] = []
        for name in selected:
            ext = EXTRACTORS[name]
            # Fan the canonical flags (--repo, --max, …) out to this
            # extractor's private dests before availability / extraction.
            apply_canonical(args, ext)
            if not ext.is_available(args):
                print(f"  skipped {name}  (not available in this env)")
                continue
            print(f"  running {name} ...")
            section = ext.extract(args)
            if section.is_empty:
                print(f"  {name}: no data")
                continue
            sections.append(section)

        if not sections:
            raise CliError("nothing extracted — every enabled extractor returned empty")

        md = render_markdown(company=args.company, sections=sections)
        blob_name = args.blob_name or f"knowledge:{args.company}"

        store = make_store(args.storage, file_root=Path(args.root))
        ref = store.put(blob_name, md, category="knowledge")
        print(f"\nwrote blob '{ref.name}' " f"({ref.byte_count} bytes, {len(sections)} sections) " f"via store={args.storage}")

        if args.out_json:
            json_path = Path(args.out_json)
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(render_json(company=args.company, sections=sections))
            print(f"wrote {json_path}")

        if args.merge_claude_md:
            self._merge_claude_md(args, sections)

        return 0

    def _merge_claude_md(self, args: argparse.Namespace, sections: List[ExtractedSection]) -> None:
        """Write the full bundle to a local detail file and splice a
        short pointer index into CLAUDE.md.

        The detail file is always written to the local filesystem (under
        the cwd) regardless of ``--storage``: the on-demand reference in
        CLAUDE.md only resolves for a session running from the project
        root, so a postgres-backed run still needs the local copy here."""
        detail_path = _CLAUDE_MD_DETAIL_ROOT / f"{_company_slug(args.company)}.md"
        detail_path.parent.mkdir(parents=True, exist_ok=True)
        detail_path.write_text(render_markdown(company=args.company, sections=sections))

        block = ClaudeMdMerger.index_block(
            company=args.company,
            detail_path=detail_path.as_posix(),
            sections=sections,
            when=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        )
        claude_md = Path(args.claude_md_path)
        existing = claude_md.read_text() if claude_md.exists() else ""
        claude_md.parent.mkdir(parents=True, exist_ok=True)
        claude_md.write_text(ClaudeMdMerger.merge(existing=existing, block=block))
        print(f"merged knowledge index into {claude_md} (detail: {detail_path})")
