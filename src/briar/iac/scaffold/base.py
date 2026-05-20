"""ScaffoldTemplate contract."""

from __future__ import annotations

import argparse
from typing import Any, ClassVar, Dict


class ScaffoldTemplate:
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Subclasses extend the per-template argparse contract."""

    def build(self, args: argparse.Namespace) -> Dict[str, Any]:
        raise NotImplementedError
