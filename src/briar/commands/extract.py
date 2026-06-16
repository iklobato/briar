"""`briar extract` — run one or more knowledge extractors, write the
result to a local markdown file."""

from __future__ import annotations

import argparse

from briar.commands.base import Command
from briar.extract import EXTRACTORS
from briar.service import extract as extract_service
from briar.storage import KNOWLEDGE_STORE_NAMES


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
        for ext in EXTRACTORS.values():
            ext.add_arguments(parser)

    def run(self, args: argparse.Namespace) -> int:
        # The operator already typed the command — execute, don't dry-run.
        # Per-extractor flags live on `args`; pass them through as overrides
        # so the service's synthesized namespaces see the same values.
        outcome = extract_service.run_extract(
            company=args.company,
            include=args.include or None,
            storage=args.storage,
            blob_name=args.blob_name,
            root=args.root,
            out_json=args.out_json,
            extractor_args=vars(args),
        )
        print(outcome.summary)
        if outcome.result and outcome.result.get("json_path"):
            print(f"wrote {outcome.result['json_path']}")
        return 0
