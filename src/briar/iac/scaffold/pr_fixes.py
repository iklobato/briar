"""PR-fixes scaffold — defaults to `pr-fixer` archetype + `one-shot`
shape (no human gate; fires hourly on cron).

Example:

    briar scaffold pr-fixes \\
        --prefix acme-prfix --owner iklobato --repo lightapi \\
        --source github \\
        --trigger-kind schedule_cron --schedule "0 * * * *"
"""

from __future__ import annotations

import argparse
from typing import Any, Dict

from briar.iac.scaffold._composer import (
    add_common_arguments,
    attach_source_arguments,
    attach_trigger_arguments,
    compose_bundle,
)
from briar.iac.scaffold.base import ScaffoldTemplate


class ScaffoldPrFixes(ScaffoldTemplate):
    name = "pr-fixes"
    description = (
        "Read PR review comments, push fixes, reply (no human gate)."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--owner", required=True)
        parser.add_argument("--repo", required=True)
        add_common_arguments(parser)
        attach_source_arguments(parser)
        attach_trigger_arguments(parser)
        # The defaults for this scaffold differ from `implementation`.
        parser.set_defaults(archetype="pr-fixer", shape="one-shot")

    def build(self, args: argparse.Namespace) -> Dict[str, Any]:
        if not args.source:
            args.source = ["github"]
        return compose_bundle(args, target=f"{args.owner}/{args.repo}")
