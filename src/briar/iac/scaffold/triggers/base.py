"""`TriggerTemplate` contract — Strategy shared by every trigger kind."""

from __future__ import annotations

import argparse
from typing import Any, ClassVar, Dict, Optional


class TriggerTemplate:
    """Subclasses set `kind` + implement `build_trigger`.

    A trigger template returns either a trigger dict (which the
    reconciler upserts as a `Trigger` row) or `None` to signal "no
    trigger row — invocation is manual"."""

    kind: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Default: no extra flags."""

    def build_trigger(
        self,
        args: argparse.Namespace,
        key_prefix: str,
        workflow_key: str,
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError
