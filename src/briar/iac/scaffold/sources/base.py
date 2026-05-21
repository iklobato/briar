"""SourceTemplate contract — Strategy pattern shared by every source kind.

A source template knows:
- its ``kind`` (e.g. ``"github"``, ``"jira"``, ``"aws"``)
- its ``family`` (``"tracker"`` or ``"cloud"``) — used by the scaffold to
  decide whether the agent should be given action tools for it
- how to build a `Source` dict (``build_source``)
- how to build the matching action `Tool` dicts (``build_tools``) — for a
  cloud source this is typically empty (read-only context); for a tracker
  it's a list of comment/transition tools

Implementations live in sibling modules and self-register into
`SOURCE_TEMPLATES` via the package `__init__`."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, List, Literal


class SourceTemplate(ABC):
    """Abstract base. Subclasses set the class attributes + implement
    `build_source`. `build_tools` defaults to an empty list (read-only
    source); override for trackers."""

    kind: ClassVar[str] = ""
    family: ClassVar[Literal["tracker", "cloud", ""]] = ""
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
