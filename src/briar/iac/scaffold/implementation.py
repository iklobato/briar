"""Implementation scaffold — defaults to `engineer` archetype + the
`plan-approve-act` workflow shape. Sources / triggers / model are
pluggable via the composer's flags.

Examples:

    # github tracker, OAuth, webhook trigger, plan→approve flow
    briar scaffold implementation \\
        --prefix acme-impl --owner iklobato --repo lightapi \\
        --source github

    # github + jira + aws sources, hourly cron, one-shot agent
    briar scaffold implementation \\
        --prefix acme-hourly --owner iklobato --repo lightapi \\
        --source github --source jira --source aws \\
        --shape one-shot --trigger-kind schedule_cron --schedule "0 * * * *"
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


class ScaffoldImplementation(ScaffoldTemplate):
    name = "implementation"
    description = (
        "Issue → plan → approve → implement / open PR (configurable)"
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--owner", required=True, help="GitHub org/user")
        parser.add_argument("--repo", required=True, help="GitHub repo name")
        add_common_arguments(parser)
        attach_source_arguments(parser)
        attach_trigger_arguments(parser)

    def build(self, args: argparse.Namespace) -> Dict[str, Any]:
        if not args.source:
            args.source = ["github"]
        return compose_bundle(args, target=f"{args.owner}/{args.repo}")
