"""KnowledgeExtractor contract.

An extractor pulls data from one source family (GitHub, AWS, the local
checkout, …) and emits an `ExtractedSection`. The composer concatenates
sections into a single per-company markdown blob agents read.

Empty sections are represented by `ExtractedSection(title="")` — the
composer skips them — instead of `Optional[ExtractedSection]`."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List


@dataclass
class ExtractedSection:
    """One section of the final markdown bundle.

    `title=""` is the "no data" sentinel — composer/orchestrator skip
    sections whose title is empty, so callers never need a None check."""

    title: str = ""
    body: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    subsections: List["ExtractedSection"] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.title


# Module-level constant — `return EMPTY_SECTION` is more readable than
# `return ExtractedSection()` at every "no data" site.
EMPTY_SECTION = ExtractedSection()


class KnowledgeExtractor(ABC):
    """Strategy contract. Subclasses set the class attributes +
    implement `extract`."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    requires_github: ClassVar[bool] = False
    requires_aws: ClassVar[bool] = False

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Subclasses contribute their own CLI flags. Default: no-op."""

    def is_available(self, args: argparse.Namespace) -> bool:
        """Return False to skip this extractor in the current env (no
        AWS profile, no GITHUB_TOKEN, etc.). The composer logs a skip
        notice instead of raising."""
        return True

    @abstractmethod
    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        """Pull data + return one section. Use `EMPTY_SECTION` when
        the extractor had nothing to report — the composer skips it."""
