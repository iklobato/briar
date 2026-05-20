"""`briar extract` — run one or more knowledge extractors, write the
result to a local markdown file."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

from briar.commands.base import Command
from briar.errors import CliError
from briar.extract import EXTRACTORS
from briar.extract.base import ExtractedSection
from briar.extract.composer import render_json, render_markdown
from briar.storage import KNOWLEDGE_STORE_NAMES, make_store


class CommandExtract(Command):
    name = "extract"
    help = (
        "Mine the live state of GitHub / AWS / etc. into a markdown "
        "knowledge blob written to local disk."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--company", required=True,
            help="Company name (drives the markdown title + blob name)",
        )
        parser.add_argument(
            "--include", action="append", default=[],
            choices=sorted(EXTRACTORS.keys()),
            help="Which extractor(s) to run (repeatable; default: all available)",
        )
        parser.add_argument(
            "--storage", default="file",
            choices=list(KNOWLEDGE_STORE_NAMES),
            help="Where to write the result (default: file)",
        )
        parser.add_argument(
            "--blob-name", default=None,
            help="Storage blob name (default: knowledge:<company>)",
        )
        parser.add_argument(
            "--root", default="./knowledge",
            help="Local file root (only used when --storage=file)",
        )
        parser.add_argument(
            "--out-json", default=None,
            help="Optional parallel JSON output path (local file only)",
        )
        for ext in EXTRACTORS.values():
            ext.add_arguments(parser)

    def run(self, args: argparse.Namespace) -> int:
        selected = args.include or list(EXTRACTORS.keys())

        sections: List[ExtractedSection] = []
        for name in selected:
            ext = EXTRACTORS[name]
            if not ext.is_available(args):
                print(f"  skipped {name}  (not available in this env)")
                continue
            print(f"  running {name} ...")
            section = ext.extract(args)
            if section is not None:
                sections.append(section)
            else:
                print(f"  {name}: no data")

        if not sections:
            raise CliError(
                "nothing extracted — every enabled extractor returned empty"
            )

        md = render_markdown(company=args.company, sections=sections)
        blob_name = args.blob_name or f"knowledge:{args.company}"

        store = make_store(args.storage, file_root=Path(args.root))
        ref = store.put(blob_name, md, category="knowledge")
        print(
            f"\nwrote blob '{ref.name}' "
            f"({ref.byte_count} bytes, {len(sections)} sections) "
            f"via store={args.storage}"
        )

        if args.out_json:
            json_path = Path(args.out_json)
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                render_json(company=args.company, sections=sections)
            )
            print(f"wrote {json_path}")

        return 0
