"""Manual trigger — no trigger row; invocation is via `briar tasks create`."""

from __future__ import annotations

import argparse
from typing import Any, Dict, Optional

from briar.iac.scaffold.triggers.base import TriggerTemplate


class TriggerManual(TriggerTemplate):
    kind = "manual"
    description = "No trigger row; invoke via `briar tasks create`"

    def build_trigger(
        self,
        args: argparse.Namespace,
        key_prefix: str,
        workflow_key: str,
    ) -> Optional[Dict[str, Any]]:
        return None
