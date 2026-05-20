"""`TriggerTemplate` contract — Strategy shared by every trigger kind."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict


class TriggerTemplate(ABC):
    """Subclasses set `kind` + implement `build_trigger`.

    `build_trigger` returns the trigger dict or `{}` to signal "no
    trigger row — invocation is manual". Callers check truthiness."""

    kind: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Default: no extra flags."""

    @abstractmethod
    def build_trigger(self, args: argparse.Namespace, key_prefix: str, workflow_key: str) -> Dict[str, Any]:
        """Emit the trigger dict, or `{}` when invocation is manual."""
