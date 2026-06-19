"""SourceTemplate contract — Strategy pattern shared by every source kind.

A source template knows:
- its ``kind`` (e.g. ``"github"``, ``"jira"``, ``"aws"``)
- how to build a `Source` dict (``build_source``)
- how to build the matching action `Tool` dicts (``build_tools``) — for a
  cloud source this is typically empty (read-only context); for a tracker
  it's a list of comment/transition tools

Implementations live in sibling modules and self-register into
`SOURCE_TEMPLATES` via the package `__init__`."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, List

from briar.errors import ConfigError


class SourceTemplate(ABC):
    """Abstract base. Subclasses set the class attributes + implement
    `build_source`. `build_tools` defaults to an empty list (read-only
    source); override for trackers."""

    kind: ClassVar[str] = ""
    # The argparse attribute holding the per-source PAT secret-id. Set
    # on subclasses that support `--auth-mode pat` (github, bitbucket,
    # sentry); empty for sources that only support OAuth/PAT-only.
    auth_secret_arg: ClassVar[str] = ""
    default_provider_for_oauth: ClassVar[str] = ""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Subclasses add their own flags (e.g. `--jira-secret-id`)."""

    @abstractmethod
    def build_source(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> Dict[str, Any]:
        """Emit the source dict the scaffold composer will include."""

    def build_tools(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> List[Dict[str, Any]]:
        """Default: source contributes no action tools. Override for
        trackers to expose comment/transition/etc. tools."""
        return []

    def target(self, args: argparse.Namespace) -> str:
        """Human-readable identifier for this source (e.g. ``owner/repo``).

        Used by ``ScaffoldComposer`` to interpolate ``{target}`` into the
        archetype's role/goal/backstory. The default returns ``""`` because
        cloud sources (AWS) don't carry a single target string. Tracker
        sources (GitHub, Bitbucket, Jira) override this to return their
        repo / project identifier; the scaffold template picks the first
        non-empty result among the selected sources."""
        return ""

    def _auth(self, args: argparse.Namespace) -> Dict[str, Any]:
        """Build the auth payload shared between every source that
        supports the PAT-or-OAuth flag pair. Subclasses set
        ``auth_secret_arg`` + ``default_provider_for_oauth``; this
        helper handles the rest. Was duplicated verbatim across
        github / bitbucket / sentry sources before the hoist."""
        ns = vars(args)
        mode = ns.get("auth_mode") or "oauth"
        if mode == "pat":
            secret_id = ns.get(self.auth_secret_arg) if self.auth_secret_arg else None
            if not secret_id:
                raise ConfigError(f"--source {self.kind} with --auth-mode pat requires " f"--{self.kind}-secret-id <secret-uuid>")
            return {"credentials_ref": secret_id, "credential_binding": None}
        return {
            "credentials_ref": None,
            "credential_binding": {
                "kind": "oauth_connection",
                "provider": self.default_provider_for_oauth or self.kind,
            },
        }

    _USER_FILTER_FIELDS = ("authors_allow", "authors_block", "assignees_allow", "assignees_block")

    def _user_filters(self, args: argparse.Namespace) -> Dict[str, List[str]]:
        """Standard 4-field user-filter dict (authors/assignees × allow/block).
        The per-source flag ``{kind}_authors_allow`` wins when set; otherwise
        the shared ``--authors-allow`` (dest ``authors_allow``) applies, so one
        filter set can cover every selected source."""
        ns = vars(args)
        return {field: self._filter_value(ns, field) for field in self._USER_FILTER_FIELDS}

    def _filter_value(self, ns: Dict[str, Any], field: str) -> List[str]:
        specific = list(ns.get(f"{self.kind}_{field}") or [])
        return specific or list(ns.get(field) or [])
