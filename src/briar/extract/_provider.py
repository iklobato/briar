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
    body: str = ""  # PR description / body, capped at the boundary
    # Diffstat — only populated by the single-PR GET (`get_pull`), NOT by
    # the list endpoint (GitHub/Bitbucket both omit it from list payloads).
    # Defaults keep `list_pulls`-built instances back-compatible. The
    # `pr-hygiene` extractor hydrates a capped sample via `get_pull`.
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0


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
    # Optional fields the `ci-health` extractor uses for flaky detection
    # (a workflow+branch flipping conclusion across runs) and duration
    # trend. Defaults keep `list_ci_runs` instances built before these
    # existed back-compatible.
    updated_at: str = ""
    run_attempt: int = 1


@dataclass(frozen=True)
class ReviewComment:
    """One inline review-thread comment on a PR. Has a file + line
    reference, unlike a generic top-level issue/PR comment."""

    id: str
    author: str
    body: str
    file_path: str = ""  # empty for top-level (non-inline) comments
    line: int = 0  # 0 for top-level
    is_resolved: bool = False
    created_at: str = ""


@dataclass(frozen=True)
class CiFailure:
    """One failed CI step on a PR. Carries enough log tail for the
    agent to diagnose without re-fetching."""

    workflow: str  # the workflow name (e.g. "test", "build")
    job: str  # the failing job inside the workflow
    step: str  # the specific step that failed
    log_tail: str  # last ~80 lines of the failing step's log
    url: str = ""


@dataclass(frozen=True)
class Commit:
    """One repository commit with its file list. Used by the
    code-hotspots extractor to find files that co-change."""

    sha: str
    author: str
    message: str  # first line / subject
    created_at: str
    file_paths: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SecurityAlert:
    """One dependency vulnerability alert (GitHub Dependabot, Bitbucket
    security scan, …). Used by the `dependency-health` extractor."""

    package: str
    severity: str  # critical | high | medium | low
    summary: str
    state: str  # open | fixed | dismissed
    manifest: str = ""  # path of the manifest declaring the dep


@dataclass(frozen=True)
class ScanAlert:
    """One static-analysis / code-scanning finding (GitHub CodeQL,
    Bitbucket Code Insights, …). Used by the `code-scanning` extractor."""

    rule_id: str
    severity: str  # critical | high | medium | low | warning | note
    file_path: str
    message: str
    state: str = "open"  # open | fixed | dismissed


@dataclass(frozen=True)
class BranchProtection:
    """Branch-protection posture for one branch. `exists=False` means the
    branch has no protection rule at all (the strongest governance smell).
    Used by the `repo-governance` extractor."""

    branch: str
    exists: bool
    required_reviews: int = 0
    requires_status_checks: bool = False
    enforce_admins: bool = False
    requires_code_owner_review: bool = False


@dataclass(frozen=True)
class Release:
    """One published release / tag. Used by the `release-cadence`
    extractor to compute shipping frequency."""

    tag: str
    name: str
    created_at: str
    is_prerelease: bool = False


@dataclass(frozen=True)
class CodeSearchHit:
    """One code-search match (file + match count). Used by the
    `todo-density` extractor."""

    file_path: str
    matches: int = 1


@dataclass(frozen=True)
class TreeEntry:
    """One entry in the repository file tree. Used by the
    `test-discipline` extractor to compute the test-to-source ratio."""

    path: str
    is_file: bool = True


class RepositoryProvider(ABC):
    """One vendor's (GitHub, Bitbucket, GitLab, …) repo surface.

    Abstract verbs: list_pulls / read_file / resolve_token / clone_url /
    authed_clone_url / pr_creation_recipe — the lowest common denominator
    every provider must support.

    Optional verbs (list_environments / list_deployments / list_ci_runs)
    have empty defaults because a SourceHut-style minimal provider can
    skip them and the extractor's section renders empty."""

    kind: ClassVar[str] = ""

    @property
    def company(self) -> str:
        """Per-company tag this provider was built for, if any.

        Concrete subclasses store this in `_company` for historical
        reasons; the public property is the canonical accessor so
        callers can stop reaching into the private attribute (the
        `getattr(provider, "_company")` reach in commands/agent.py was
        a Demeter smell that Phase 10 cleaned up). Default returns the
        instance's `_company` attribute when present, empty otherwise."""
        return str(getattr(self, "_company", "") or "")

    @abstractmethod
    def is_available(self) -> bool:
        """True iff credentials are present and the provider is usable
        for the company this provider was built for. Extractors gate
        their `KnowledgeExtractor.is_available()` on this — a missing
        token short-circuits the extractor instead of hitting a 401."""

    # ---- clone + auth seam (used by `briar agent`) -----------------------
    #
    # Was the `RepoCloner` ABC before unification. All four methods are
    # cheap pure-string returns — no I/O — so they're safe to call from
    # any thread or context.

    @abstractmethod
    def resolve_token(self) -> str:
        """Credential string for this provider, scoped to the company
        this instance was built for. Empty when not configured — the
        caller logs a clear error and bails (no exception thrown here)."""

    @abstractmethod
    def clone_url(self, owner: str, repo: str) -> str:
        """Canonical HTTPS clone URL with no auth embedded. Used to
        reset `origin` after cloning so the token does not persist in
        `.git/config`."""

    @abstractmethod
    def authed_clone_url(self, owner: str, repo: str, token: str) -> str:
        """Clone URL with the token embedded per the vendor's auth
        convention (`x-access-token@` for GitHub, `x-token-auth@` for
        Bitbucket, …). The actual URL passed to `git clone`."""

    @abstractmethod
    def pr_creation_recipe(self, *, owner: str, repo: str, branch: str) -> str:
        """Procedure lines 6-7 of the engineer archetype's instruction
        string — the vendor-specific recipe for opening a draft PR.
        Returned as ready-to-splice markdown."""

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

    # ---- task-scoped verbs (used by FetchPrReviewContext) ----------------
    #
    # Three concrete-default-empty verbs that providers override to deliver
    # the rich per-PR context an agent needs to fix a review or pass CI.
    # The scheduled extractors don't use these — they're for
    # `briar agent` invocations where the operator passed --pr <N>.

    def get_pull(self, repo: str, number: int) -> "PullRequest":
        """Fetch one PR by number with full detail. Default returns the
        same shape as `list_pulls` with empty-author etc. — override
        to populate real fields. Used by FetchPrReviewContext."""
        return PullRequest(
            number=number,
            title="",
            author="",
            is_draft=False,
            head_ref="",
            base_ref="",
            review_comment_count=0,
            created_at="",
        )

    def list_pr_comments(self, repo: str, number: int) -> List[ReviewComment]:
        """Return every comment on one PR — inline review-thread
        comments AND top-level issue comments. Empty default."""
        return []

    def list_ci_failures(self, repo: str, number: int) -> List[CiFailure]:
        """Return the failing CI steps for one PR with a log tail.
        Empty default. Implementations can be expensive (each failure
        requires fetching the workflow run log) — call site is one PR
        at a time, never bulk."""
        return []

    # ---- task-scoped verbs (used by ExtractCodeHotspots) -----------------

    def list_recent_commits(self, repo: str, *, since_days: int = 30, max_count: int = 200) -> List[Commit]:
        """Return recent commits with their file lists, used for
        co-change clustering. Empty default."""
        return []

    # ---- code-quality verbs (GitHub-native; empty default elsewhere) -----
    #
    # Each returns an empty list / "absent" value by default so a provider
    # without the underlying API (Bitbucket Cloud, a minimal host) simply
    # renders the corresponding extractor section empty — same graceful
    # degradation as list_environments / list_deployments above.

    def list_dependabot_alerts(self, repo: str, *, max_count: int = 200) -> List[SecurityAlert]:
        """Open dependency-vulnerability alerts. Empty default. Used by
        the `dependency-health` extractor."""
        return []

    def list_code_scanning_alerts(self, repo: str, *, max_count: int = 200) -> List[ScanAlert]:
        """Open static-analysis findings. Empty default. Used by the
        `code-scanning` extractor."""
        return []

    def get_branch_protection(self, repo: str, branch: str = "") -> BranchProtection:
        """Branch-protection posture for `branch` (default branch when
        empty). Default returns `exists=False`. Used by the
        `repo-governance` extractor."""
        return BranchProtection(branch=branch, exists=False)

    def default_branch(self, repo: str) -> str:
        """Name of the repo's default branch. Empty default — callers
        fall back to common names. Used by `repo-governance`."""
        return ""

    def list_releases(self, repo: str, *, max_count: int = 100) -> List[Release]:
        """Most-recent releases / tags. Empty default. Used by the
        `release-cadence` extractor."""
        return []

    def search_code(self, repo: str, query: str, *, max_count: int = 200) -> List[CodeSearchHit]:
        """Code-search matches for `query` within `repo`. Empty default.
        Used by the `todo-density` extractor."""
        return []

    def list_tree(self, repo: str, *, max_count: int = 5000) -> List[TreeEntry]:
        """Full file tree of the default branch. Empty default. Used by
        the `test-discipline` extractor."""
        return []

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        """Canonical env-var names this provider needs to be usable for
        the given company. The doctor (`briar secrets doctor`) uses
        this for coverage reporting; the values are never read here.
        Default: empty (provider needs no env vars — ambient credential
        chain handles auth)."""
        return []
