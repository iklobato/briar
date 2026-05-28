"""Implementation scaffold — defaults to `engineer` archetype + the
`plan-approve-act` workflow shape. Sources / triggers / model are
pluggable via the composer's flags.

Identity flags belong to each source: GitHub uses ``--owner`` /
``--repo``, Bitbucket uses ``--bitbucket-workspace`` /
``--bitbucket-repo``. The scaffold template itself is provider-agnostic
— it asks the selected sources for their target identifier.

Examples:

    # github tracker, OAuth, webhook trigger, plan→approve flow
    briar scaffold implementation \\
        --prefix acme-impl --source github \\
        --owner alice --repo widgets

    # bitbucket tracker, app-password auth, webhook trigger
    briar scaffold implementation \\
        --prefix acme-impl --source bitbucket \\
        --bitbucket-workspace acme --bitbucket-repo widgets \\
        --auth-mode pat --bitbucket-secret-id <uuid> \\
        --trigger-kind bitbucket_webhook

    # github + jira + aws sources, hourly cron, one-shot agent
    briar scaffold implementation \\
        --prefix acme-hourly --source github --source jira --source aws \\
        --owner alice --repo widgets \\
        --shape one-shot --trigger-kind schedule_cron --schedule "0 * * * *"
"""

from __future__ import annotations

import argparse
from typing import Any, Dict

from briar.iac.scaffold._composer import (
    ScaffoldResolver,
    add_common_arguments,
    attach_source_arguments,
    attach_trigger_arguments,
    compose_bundle,
)
from briar.iac.scaffold.base import ScaffoldTemplate


class ScaffoldImplementation(ScaffoldTemplate):
    name = "implementation"
    description = "Issue → plan → approve → implement / open PR (configurable)"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        add_common_arguments(parser)
        attach_source_arguments(parser)
        attach_trigger_arguments(parser)

    def build(self, args: argparse.Namespace) -> Dict[str, Any]:
        if not args.source:
            args.source = ["github"]
        target = ScaffoldResolver.target_for(args)
        return compose_bundle(args, target=target)
