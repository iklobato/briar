"""ScaffoldTemplate contract."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict


class ScaffoldTemplate(ABC):
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Subclasses extend the per-template argparse contract."""

    @abstractmethod
    def build(self, args: argparse.Namespace) -> Dict[str, Any]:
        """Emit the JSON-serialisable config bundle."""
