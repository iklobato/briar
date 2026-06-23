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
    # `requires_github` / `requires_aws` are read ONLY by the dashboard
    # collector (dashboard/collectors.py:SourcesCollector) for the
    # secrets-doctor inventory surface. The 4 other `requires_*` flags
    # that used to live on the base + *Backed subclasses were dead
    # ClassVars (zero readers) — Phase 13 deleted them.
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
        """Pull data + return one section. Use `empty_section()` when
        the extractor had nothing to report — the composer skips it."""

    def provider_class_for(self, args: argparse.Namespace):
        """Return the provider CLASS this extractor uses for the given
        runbook args, or None if it's not provider-backed (file/local
        extractors). Used by ``briar secrets doctor`` to query
        ``provider_class.required_env_vars(company)`` without having
        to maintain a parallel (extractor × provider) cred table.
        Default: None. Each ``*BackedExtractor`` base overrides."""
        return None


# ─── *Backed* shared helpers ──────────────────────────────────────────
#
# Every *Backed*Extractor (Cloud / Tracker / Meeting / Repo) and the 3
# TaskScoped variants of them share the same three-method shape:
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
    **extra,
):
    """Build a provider instance from `args.<flag>` via `make_fn`.

    `company` is sourced from `args.company` (set by the runbook
    executor before calling `extract`); for one-shot `briar extract`
    it defaults to empty."""
    ns = vars(args)
    kind = ns.get(flag) or default
    company = ns.get("company") or ""
    return make_fn(kind, company=company, **extra)


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

    def _cloud(self, args: argparse.Namespace):
        from briar.extract._clouds import make_cloud

        ns = vars(args)
        return _resolve_provider_from_args(
            args,
            flag="cloud",
            default="aws",
            make_fn=make_cloud,
            region=ns.get("aws_extract_region") or ns.get("cloud_region") or "",
            profile=ns.get("aws_extract_profile") or ns.get("cloud_profile") or "",
        )

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

    def _tracker(self, args: argparse.Namespace):
        from briar.extract._trackers import make_tracker

        return _resolve_provider_from_args(args, flag="tracker", default="jira", make_fn=make_tracker)

    def provider_class_for(self, args: argparse.Namespace):
        from briar.extract._trackers import TRACKERS

        return _provider_class_for_flag(args, flag="tracker", default="jira", classes_dict=TRACKERS)


class TaskScopedExtractor(ABC):
    """JIT-invoked extractor — fetched at agent-invocation time for one
    specific task (a single ticket, a single PR), NOT pre-baked into
    the per-company markdown blob.

    Distinct from `KnowledgeExtractor` because the lifecycle differs:

      ``KnowledgeExtractor.extract(args)``  runs on a cron-equivalent
                                            schedule; output goes into
                                            ``./knowledge/<company>.md``
                                            and applies to every task.
      ``TaskScopedExtractor.fetch(args)``   runs once at agent
                                            invocation; output is
                                            spliced into ONE agent's
                                            system prompt only.

    Same `ExtractedSection` output shape so the agent prompt builder
    can treat both uniformly. Concrete subclasses pick a partial base
    below (``TaskScopedTrackerExtractor`` / ``TaskScopedRepoExtractor``)
    that registers the right ``--tracker`` / ``--provider`` flag and
    factory helper."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    # Same role as `KnowledgeExtractor.heading` — splicer uses this to
    # find the JIT section in the agent's prompt. Subclasses MUST
    # override when their output is consumed by an archetype.
    heading: ClassVar[str] = ""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Default: no extra arguments. Concrete subclasses register
        their identity flags (--ticket-key, --pr-number, etc.) here."""

    @abstractmethod
    def fetch(self, args: argparse.Namespace) -> ExtractedSection:
        """Pull JIT context + return one section. Use ``empty_section()``
        when there's nothing to report. Called once per agent run, NOT
        on the runbook schedule."""


class TaskScopedTrackerExtractor(TaskScopedExtractor):
    """TaskScoped + tracker-backed. Same shape as
    `TrackerBackedExtractor` but for the JIT lifecycle."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        from briar.extract._trackers import TrackerRegistry

        _register_provider_flag(
            parser,
            flag="tracker",
            default="jira",
            choices=list(TrackerRegistry.kinds()),
            help_text="Tracker provider this extractor uses (default: jira)",
        )

    def _tracker(self, args: argparse.Namespace):
        from briar.extract._trackers import make_tracker

        return _resolve_provider_from_args(args, flag="tracker", default="jira", make_fn=make_tracker)


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

    def _meeting(self, args: argparse.Namespace):
        from briar.extract._meetings import make_meeting

        return _resolve_provider_from_args(args, flag="meeting", default="fireflies", make_fn=make_meeting)

    def provider_class_for(self, args: argparse.Namespace):
        from briar.extract._meetings import MEETINGS

        return _provider_class_for_flag(args, flag="meeting", default="fireflies", classes_dict=MEETINGS)


class TaskScopedMeetingExtractor(TaskScopedExtractor):
    """TaskScoped + meeting-backed. Same shape as
    `MeetingBackedExtractor` but for the JIT lifecycle."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        from briar.extract._meetings import meeting_kinds

        _register_provider_flag(
            parser,
            flag="meeting",
            default="fireflies",
            choices=list(meeting_kinds()),
            help_text="Meeting provider this extractor uses (default: fireflies)",
        )

    def _meeting(self, args: argparse.Namespace):
        from briar.extract._meetings import make_meeting

        return _resolve_provider_from_args(args, flag="meeting", default="fireflies", make_fn=make_meeting)


class TaskScopedChatExtractor(TaskScopedExtractor):
    """TaskScoped + chat-backed. Same shape as `TaskScopedMeetingExtractor`
    but for team-chat providers (Slack, …)."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        from briar.extract._chats import chat_kinds

        _register_provider_flag(
            parser,
            flag="chat",
            default="slack",
            choices=list(chat_kinds()),
            help_text="Chat provider this extractor uses (default: slack)",
        )

    def _chat(self, args: argparse.Namespace):
        from briar.extract._chats import make_chat

        return _resolve_provider_from_args(args, flag="chat", default="slack", make_fn=make_chat)


class TaskScopedRepoExtractor(TaskScopedExtractor):
    """TaskScoped + repo-backed."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        from briar.extract._providers import RepositoryProviderRegistry

        _register_provider_flag(
            parser,
            flag="provider",
            default="github",
            choices=list(RepositoryProviderRegistry.kinds()),
            help_text="Repository provider this extractor uses (default: github)",
        )

    def _provider(self, args: argparse.Namespace):
        from briar.extract._providers import make_provider

        return _resolve_provider_from_args(args, flag="provider", default="github", make_fn=make_provider)

    def provider_class_for(self, args: argparse.Namespace):
        from briar.extract._providers import PROVIDERS

        return _provider_class_for_flag(args, flag="provider", default="github", classes_dict=PROVIDERS)


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

    def _provider(self, args: argparse.Namespace):
        from briar.extract._providers import make_provider

        return _resolve_provider_from_args(args, flag="provider", default="github", make_fn=make_provider)

    def provider_class_for(self, args: argparse.Namespace):
        from briar.extract._providers import PROVIDERS

        return _provider_class_for_flag(args, flag="provider", default="github", classes_dict=PROVIDERS)
