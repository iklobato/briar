"""`MessageWriter` — vendor-neutral write surface.

Symmetric to `TrackerProvider` / `RepositoryProvider` but for outbound
writes instead of reads. Each adapter wraps one vendor's write API
(Jira comment, Slack channel post, Telegram chat message, …).

Strategy + Registry. Adapters live in `messaging/` siblings; the
`WRITERS` registry is built via `build_registry()` so a duplicate
`kind` collision raises at import time.

Two consumers today:
- `briar agent` invokes writers via the `SendMessageTool` (the LLM
  picks a configured channel by name; the tool resolves the channel
  to a writer + calls `send`).
- Future scheduler hooks (deferred) — extract-success notifications,
  status broadcasts.

Each runbook company can declare named writer bindings under
``messages:`` (see `iac/runbook/models.py:MessageBinding`); the
agent picks them up via the company-scoped config injected at run
time."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Tuple


log = logging.getLogger(__name__)


def parse_pr_target(target: str, extras: Dict[str, Any]) -> Tuple[str, int]:
    """Parse a `owner/repo#42` or `workspace/repo#42` form into
    `(repo_addr, number)`. Falls back to `target` + `extras["pr"]`
    when no `#` is present. Returns `("", 0)` for malformed input.

    Shared by `GithubPrCommentWriter` and `BitbucketPrCommentWriter` —
    same format, same parsing rules, same fallback path."""
    if "#" in target:
        addr, _, n = target.rpartition("#")
        try:
            return addr, int(n)
        except ValueError:
            return "", 0
    n_extras = extras.get("pr")
    if target and n_extras is not None:
        try:
            return target, int(n_extras)
        except (TypeError, ValueError):
            return "", 0
    return "", 0


def with_ai_prefix(body: str) -> str:
    """Prepend `[AI] ` to comments the LLM posts on the operator's behalf.
    Per-operator CLAUDE.md mandate: PR/issue/ticket comments must be
    marked. Idempotent — passes through bodies that already start with
    the marker (allowing the LLM to author it explicitly without
    producing `[AI] [AI] ...`)."""
    if not body:
        return body
    if body.lstrip().startswith("[AI]"):
        return body
    return f"[AI] {body}"


@dataclass(frozen=True)
class SendResult:
    """Outcome of one write. `ok=False` is logged but does NOT raise —
    same fire-and-forget semantics as `NotificationSink.send`.
    Caller branches on `ok` for retries / chained behaviour."""

    ok: bool
    detail: str = ""
    ref: str = ""  # provider-side message id when known (Jira comment id, Slack ts, …)


class MessageWriter(ABC):
    """Strategy contract for one vendor's outbound write."""

    kind: ClassVar[str] = ""

    @abstractmethod
    def is_available(self) -> bool:
        """True iff the writer has the credentials it needs (env vars
        + any binding-level config) to perform a real send."""

    @abstractmethod
    def send(self, *, target: str, body: str, **extras: Any) -> SendResult:
        """Send `body` to `target`. `target` is vendor-specific:

        - Jira-comment:   ``PROJ-123`` (ticket key)
        - Jira-transition: ``PROJ-123`` (ticket key) + ``extras["status"]=...``
        - Slack-channel:  the channel is per-binding config (the
                          target arg can override with a different
                          channel ID at send time)
        - Telegram-chat:  the chat is per-binding config (same)
        - GH/BB PR comment: ``owner/repo#42`` + optional inline
                          ``extras["file_path"]`` + ``extras["line"]``

        Implementations should not raise on transient network errors —
        return ``SendResult(ok=False, detail="...")`` and let the
        caller decide on retry."""

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        """Same contract as `RepositoryProvider.required_env_vars` —
        env-var names this writer needs. Empty default (writers that
        configure entirely via the runbook binding's `config` dict
        don't need env vars)."""
        return []


@dataclass(frozen=True)
class MessageBindingResolved:
    """A runbook ``messages:`` entry post-resolution. The runbook YAML
    holds `kind` + freeform `config`; this is the typed version that
    the agent tool + scheduler hooks see."""

    handle: str  # the YAML key (e.g. "ticket_comment", "ops_chat")
    kind: str  # registered writer kind
    company: str
    config: Dict[str, Any] = field(default_factory=dict)
