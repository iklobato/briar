"""KnowledgeExtractor contract.

An extractor pulls data from one source family (GitHub, AWS, the local
checkout, …) and emits an `ExtractedSection`. The composer concatenates
sections into a single per-company markdown blob the agents use as
context on every run.

Each extractor knows whether it can run in the current environment
(`is_available()`) and what fields it needs from the YAML / CLI flags.
"""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional


@dataclass
class ExtractedSection:
    """One section of the final markdown bundle.

    The composer renders this as:
        ## <title>
        <body>
        (followed by optional sub-sections)
    """

    title: str
    body: str = ""
    # Structured payload mirroring the markdown — useful for JSON output
    # mode and for downstream programmatic consumers.
    data: Dict[str, Any] = field(default_factory=dict)
    # Optional "subsections" rendered as `### <name>` under the section.
    subsections: List["ExtractedSection"] = field(default_factory=list)


class KnowledgeExtractor(ABC):
    """Strategy contract. Subclasses set the four class attributes +
    implement `extract`."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    requires_github: ClassVar[bool] = False
    requires_aws: ClassVar[bool] = False

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Subclasses contribute their own CLI flags. Default: no-op."""

    def is_available(self, args: argparse.Namespace) -> bool:
        """Return False to skip this extractor in the current env (e.g.
        no AWS profile set, no GITHUB_TOKEN, etc.). The composer prints
        a one-line skip notice rather than raising."""
        return True

    @abstractmethod
    def extract(self, args: argparse.Namespace) -> Optional[ExtractedSection]:
        """Pull data and return one section. Return `None` to silently
        omit (useful when the extractor is enabled but had nothing to
        report — e.g. a repo with zero merged PRs)."""
