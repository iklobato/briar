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
    requires_repository_provider: ClassVar[bool] = False

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


class RepoBackedExtractor(KnowledgeExtractor):
    """Base class for extractors that talk to a code host (GitHub,
    Bitbucket, …). Adds the shared ``--provider`` flag and a
    `_provider(args)` helper that hands back a configured
    `RepositoryProvider`. The runbook executor sets ``args.company`` so
    per-tenant provider creds (e.g. ``BITBUCKET_<COMPANY>_*``) resolve
    correctly.

    Concrete subclasses still implement their own ``extract`` / their
    own ``add_arguments`` for repo lists + filters, but they call the
    provider's verbs (``list_pulls`` / ``read_file`` / …) instead of
    hard-coding `GithubApi`. Adding a Bitbucket-aware extractor =
    inherit + implement; the provider lookup is shared."""

    requires_repository_provider: ClassVar[bool] = True

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Subclasses MUST call ``super().add_arguments(parser)`` if
        they override this — that's how the shared ``--provider`` flag
        gets registered.

        Idempotent: the one-shot ``briar extract`` command shares one
        parser across every extractor, so every `RepoBackedExtractor`
        would otherwise try to register ``--provider`` four times. The
        guard catches the second-and-later registrations silently —
        same flag, same default, same choices, no behavioural drift."""
        from briar.extract._providers import RepositoryProviderRegistry

        try:
            parser.add_argument(
                "--provider",
                default="github",
                choices=list(RepositoryProviderRegistry.kinds()),
                help="Repository provider this extractor uses (default: github)",
            )
        except argparse.ArgumentError:
            pass

    def _provider(self, args: argparse.Namespace):
        """Resolve the provider for this extraction run. ``args.company``
        is set by the runbook executor before calling ``extract`` — for
        one-shot ``briar extract`` it defaults to empty (GitHub treats
        company as inert; Bitbucket would report `is_available() ==
        False`)."""
        from briar.extract._providers import make_provider

        ns = vars(args)
        kind = ns.get("provider") or "github"
        company = ns.get("company") or ""
        return make_provider(kind, company=company)
