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
from typing import Any, ClassVar, Dict, List, Optional

from briar.auth.check import LoginTarget


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


def empty_section() -> ExtractedSection:
    """Sentinel factory for "no data" sections — `return empty_section()`
    at every site where the extractor had nothing to report. Returns a
    FRESH instance each call so callers that accidentally mutate
    ``.data`` or ``.subsections`` can't poison every other extractor's
    "empty" return (the previous shape was a shared singleton — a real
    footgun once any caller did ``section.data["x"] = ...``)."""
    return ExtractedSection()


class KnowledgeExtractor(ABC):
    """Strategy contract. Subclasses set the class attributes +
    implement `extract`."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    # Heading the extractor's output blob uses as its `## <heading>`
    # marker. KnowledgeSplicer reads this to slice blobs back into
    # per-extractor sections. Subclasses MUST override when their
    # output is consumed by an archetype (default empty = JIT-only or
    # not splicer-bound).
    heading: ClassVar[str] = ""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Subclasses contribute their own CLI flags. Default: no-op."""

    def login_target(self, args: argparse.Namespace) -> Optional[LoginTarget]:
        """The adapter this extractor reads through and the resources it reads, for the
        login check before a run (spec 005, J1, J5). None: no source to check."""
        return None

    def is_available(self, args: argparse.Namespace) -> bool:
        """Return False to skip this extractor in the current env (no
        repo given, no login saved, etc.). The composer logs a skip
        notice instead of raising."""
        return True

    @abstractmethod
    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        """Pull data + return one section. Use `empty_section()` when
        the extractor had nothing to report — the composer skips it."""

    def provider_class_for(self, args: argparse.Namespace):
        """Return the provider CLASS this extractor uses for the given
        runbook args, or None if it's not provider-backed (file/local
        extractors). Used by a credential coverage check (1.x
        ``briar secrets doctor``; in 2.0 the spec 005 pre-run check) to query
        ``provider_class.required_env_vars(company)`` without having
        to maintain a parallel (extractor × provider) cred table.
        Default: None. Each ``*BackedExtractor`` base overrides."""
        return None


# ─── *Backed* shared helpers ──────────────────────────────────────────
#
# Every *Backed*Extractor (Cloud / Tracker / Meeting / Repo) shares the
# same three-method shape:
#   1. `add_arguments` registers a `--<flag>` choice from the registry
#   2. `_<flag>(args)` reads `args.<flag>` + builds a provider instance
#   3. `provider_class_for` looks up the class in the registry dict
# Before the Phase 14 collapse these were duplicated 7× with only the
# flag name / default / registry varying. The helpers below own the
# shape; the concrete *Backed* classes are thin shells that supply
# the variation.


def _register_provider_flag(
    parser: argparse.ArgumentParser,
    *,
    flag: str,
    default: str,
    choices: List[str],
    help_text: str,
) -> None:
    """Idempotent ``--<flag>`` registration.

    The one-shot ``briar extract`` command shares one parser across
    every extractor, so two same-family extractors would both try to
    register the same flag. The try/except is the de-dupe guard —
    same flag, same default, same choices, no behavioural drift."""
    try:
        parser.add_argument(
            f"--{flag}",
            default=default,
            choices=choices,
            help=help_text,
        )
    except argparse.ArgumentError:
        pass


def _resolve_provider_from_args(
    args: argparse.Namespace,
    *,
    flag: str,
    default: str,
    make_fn,
):
    """Build a provider instance from `args.<flag>` via `make_fn`, with the company
    logins that `briar extract` or the runbook executor put on `args.logins`
    (spec 005)."""
    from briar.auth.logins import CompanyLogins

    kind = vars(args).get(flag) or default
    return make_fn(kind, CompanyLogins.from_args(args))


def _provider_class_for_flag(
    args: argparse.Namespace,
    *,
    flag: str,
    default: str,
    classes_dict: Dict[str, type],
):
    """Look up the registered class for `args.<flag>` — used by the
    secrets-doctor to query `provider_class.required_env_vars(company)`."""
    ns = vars(args)
    return classes_dict.get(ns.get(flag) or default)


def _provider_is_available(args: argparse.Namespace, *, gate_arg: str, resolver) -> bool:
    """Shared ``is_available`` shape for ``*BackedExtractor`` leaves:
    skip when the leaf's gate arg (e.g. ``--pr-repo``) is empty, skip
    when the provider can't be built, else defer to the provider's own
    availability check. Each leaf supplies only the gate-arg name; the
    base supplies the resolver (``_provider`` / ``_tracker``)."""
    if not getattr(args, gate_arg, None):
        return False
    try:
        provider = resolver(args)
    except Exception:  # noqa: BLE001
        return False
    return provider.is_available()


class CloudBackedExtractor(KnowledgeExtractor):
    """Base class for extractors that talk to a cloud provider (AWS,
    GCP, Azure). Registers the shared ``--cloud`` flag + helper."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        from briar.extract._clouds import CloudRegistry

        _register_provider_flag(
            parser,
            flag="cloud",
            default="aws",
            choices=list(CloudRegistry.kinds()),
            help_text="Cloud provider this extractor uses (default: aws)",
        )

    def login_target(self, args: argparse.Namespace) -> Optional[LoginTarget]:
        cloud = self._cloud(args)
        return LoginTarget(cloud, cloud.scope())

    def _cloud(self, args: argparse.Namespace):
        from briar.extract._clouds import make_cloud

        from briar.auth.logins import CompanyLogins

        ns = vars(args)
        # The login, the AWS profile, the GCP project and the Azure subscription come from
        # `[auth.<cloud>]` (spec 005, I4); the region stays an extractor setting.
        return make_cloud(ns.get("cloud") or "aws", CompanyLogins.from_args(args), region=ns.get("aws_extract_region") or ns.get("cloud_region") or "")

    def provider_class_for(self, args: argparse.Namespace):
        from briar.extract._clouds import CLOUDS

        return _provider_class_for_flag(args, flag="cloud", default="aws", classes_dict=CLOUDS)


class TrackerBackedExtractor(KnowledgeExtractor):
    """Base class for extractors that talk to an issue tracker (Jira,
    GitHub Issues, Bitbucket Issues, Linear). Symmetric to
    `RepoBackedExtractor` — same `--tracker` flag + `_tracker(args)`
    helper, just a different ABC behind it."""

    # Leaf sets the arg whose presence gates availability (e.g. "ticket_project").
    _availability_arg: ClassVar[str] = ""

    def is_available(self, args: argparse.Namespace) -> bool:
        if not self._availability_arg:
            return super().is_available(args)
        return _provider_is_available(args, gate_arg=self._availability_arg, resolver=self._tracker)

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        from briar.extract._trackers import TrackerRegistry

        _register_provider_flag(
            parser,
            flag="tracker",
            default="jira",
            choices=list(TrackerRegistry.kinds()),
            help_text="Tracker provider this extractor uses (default: jira)",
        )

    def login_target(self, args: argparse.Namespace) -> Optional[LoginTarget]:
        return LoginTarget(self._tracker(args), tuple(vars(args).get(self._availability_arg) or ()))

    def _tracker(self, args: argparse.Namespace):
        from briar.extract._trackers import make_tracker

        return _resolve_provider_from_args(args, flag="tracker", default="jira", make_fn=make_tracker)

    def provider_class_for(self, args: argparse.Namespace):
        from briar.extract._trackers import TRACKERS

        return _provider_class_for_flag(args, flag="tracker", default="jira", classes_dict=TRACKERS)


class MeetingBackedExtractor(KnowledgeExtractor):
    """Base class for extractors that talk to a meeting-transcription
    tool (Fireflies, future Otter / Granola / …). Symmetric to
    `TrackerBackedExtractor` — same `--meeting` flag + `_meeting(args)`
    helper, just a different ABC behind it."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        from briar.extract._meetings import meeting_kinds

        _register_provider_flag(
            parser,
            flag="meeting",
            default="fireflies",
            choices=list(meeting_kinds()),
            help_text="Meeting provider this extractor uses (default: fireflies)",
        )

    def login_target(self, args: argparse.Namespace) -> Optional[LoginTarget]:
        return LoginTarget(self._meeting(args))

    def _meeting(self, args: argparse.Namespace):
        from briar.extract._meetings import make_meeting

        return _resolve_provider_from_args(args, flag="meeting", default="fireflies", make_fn=make_meeting)

    def provider_class_for(self, args: argparse.Namespace):
        from briar.extract._meetings import MEETINGS

        return _provider_class_for_flag(args, flag="meeting", default="fireflies", classes_dict=MEETINGS)


class RepoBackedExtractor(KnowledgeExtractor):
    """Base class for extractors that talk to a code host (GitHub,
    Bitbucket, …). Adds the shared ``--provider`` flag and a
    `_provider(args)` helper that hands back a configured
    `RepositoryProvider`. `briar extract` and the runbook executor set
    ``args.logins`` so the provider gets the login of its company (spec 005).

    Concrete subclasses still implement their own ``extract`` / their
    own ``add_arguments`` for repo lists + filters, but they call the
    provider's verbs (``list_pulls`` / ``read_file`` / …) instead of
    hard-coding `GithubApi`. Adding a Bitbucket-aware extractor =
    inherit + implement; the provider lookup is shared.

    Subclasses MUST call ``super().add_arguments(parser)`` if they
    override it — that's how the shared ``--provider`` flag gets
    registered. The flag-registration is idempotent (`briar extract`
    shares one parser across every extractor), so the second-and-later
    re-registration is silently skipped."""

    # Leaf sets the arg whose presence gates availability (e.g. "pr_repo").
    _availability_arg: ClassVar[str] = ""

    def is_available(self, args: argparse.Namespace) -> bool:
        if not self._availability_arg:
            return super().is_available(args)
        return _provider_is_available(args, gate_arg=self._availability_arg, resolver=self._provider)

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        from briar.extract._providers import RepositoryProviderRegistry

        _register_provider_flag(
            parser,
            flag="provider",
            default="github",
            choices=list(RepositoryProviderRegistry.kinds()),
            help_text="Repository provider this extractor uses (default: github)",
        )

    def login_target(self, args: argparse.Namespace) -> Optional[LoginTarget]:
        return LoginTarget(self._provider(args), tuple(vars(args).get(self._availability_arg) or ()))

    def _provider(self, args: argparse.Namespace):
        from briar.extract._providers import make_provider

        return _resolve_provider_from_args(args, flag="provider", default="github", make_fn=make_provider)

    def provider_class_for(self, args: argparse.Namespace):
        from briar.extract._providers import PROVIDERS

        return _provider_class_for_flag(args, flag="provider", default="github", classes_dict=PROVIDERS)
