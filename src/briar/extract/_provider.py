"""`RepositoryProvider` — vendor-neutral facade the extractors use
instead of talking to GitHub / Bitbucket / GitLab / ... directly.

Strategy + Registry. Each concrete provider lives in `_providers/`
and self-registers. Extractors call
``make_provider(kind, company)`` and then invoke verbs that return the
normalised dataclasses defined here. The provider implementation is
responsible for translating its vendor-specific JSON into these
shapes.

Why dataclasses instead of dicts: the extractor used to consume PyGithub's
raw JSON (different field names per vendor — `number` vs `id`,
`user.login` vs `author.display_name`, `merged_at` vs `state == MERGED`).
A typed dataclass forces every provider to do the translation in one
place. Adding a new provider becomes "implement five verbs that
construct these dataclasses" — no extractor edits."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, List


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PullRequest:
    """Vendor-neutral pull-request shape.

    Maps GitHub `number` / `user.login` / `head.ref` / `base.ref` /
    `review_comments` / `merged_at` to the names below; Bitbucket
    `id` / `author.display_name` / `source.branch.name` /
    `destination.branch.name` / `comment_count` / `state == MERGED`
    map onto the same fields. Extractors only see this shape."""

    number: int
    title: str
    author: str
    is_draft: bool
    head_ref: str
    base_ref: str
    review_comment_count: int
    created_at: str
    merged_at: str = ""
    requested_reviewers: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Environment:
    """Deploy environment (GitHub Environments, Bitbucket Deployment
    Environments, …). Empty list when the provider lacks the concept."""

    name: str
    protection_rule_count: int
    url: str


@dataclass(frozen=True)
class Deployment:
    """One deployment event."""

    id: str
    environment: str
    sha: str
    creator: str
    created_at: str


@dataclass(frozen=True)
class CiRun:
    """One CI/pipeline run."""

    name: str
    status: str
    conclusion: str
    head_branch: str
    created_at: str


class RepositoryProvider(ABC):
    """Strategy contract. Each concrete subclass adapts one vendor
    (GitHub, Bitbucket, GitLab, …) onto the same surface so extractors
    stay provider-agnostic.

    Three verbs are abstract because every provider must support them
    (PRs and file-reads are the lowest common denominator). Three are
    concrete with empty defaults because not every provider has a
    native concept of deploy environments / deployments / CI runs —
    Bitbucket Pipelines could implement them later, GitLab CI similarly,
    but a SourceHut-style minimal provider can ignore them and the
    extractor's section just renders empty."""

    kind: ClassVar[str] = ""

    @abstractmethod
    def is_available(self) -> bool:
        """True iff credentials are present and the provider is usable
        for the company this provider was built for. Extractors gate
        their `KnowledgeExtractor.is_available()` on this — a missing
        token short-circuits the extractor instead of hitting a 401."""

    @abstractmethod
    def list_pulls(self, repo: str, *, state: str, max_count: int) -> List[PullRequest]:
        """List PRs by state. `state` is ``"open"`` | ``"merged"``.
        Implementations translate to their vendor's state vocabulary
        (GitHub: ``state=closed`` + filter `merged_at is not None`;
        Bitbucket: ``state=MERGED``). Most-recent first."""

    @abstractmethod
    def read_file(self, repo: str, path: str) -> str:
        """Read file content from the default branch. Returns ``""``
        on not-found / not-a-file / decode error so callers don't need
        try/except. Used by `codebase-conventions` to inspect manifests
        (pyproject.toml, package.json, go.mod, …)."""

    def list_environments(self, repo: str) -> List[Environment]:
        """Return deploy environments. Empty default for providers
        without a native environment concept (Bitbucket Cloud)."""
        return []

    def list_deployments(self, repo: str, *, limit: int) -> List[Deployment]:
        """Return the most-recent deployments. Empty default."""
        return []

    def list_ci_runs(self, repo: str, *, limit: int) -> List[CiRun]:
        """Return the most-recent CI / pipeline runs. Empty default."""
        return []
