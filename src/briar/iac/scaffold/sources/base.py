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
`SOURCE_TEMPLATES` via the package `__init__`.
"""

from __future__ import annotations

import argparse
from typing import Any, ClassVar, Dict, List, Literal


class SourceTemplate:
    """Abstract base. Subclasses set the four class attributes + implement
    `build_source` / `build_tools`. The default `build_tools` returns an
    empty list (read-only source); override for trackers."""

    kind: ClassVar[str] = ""
    family: ClassVar[Literal["tracker", "cloud", ""]] = ""
    auth_secret_arg: ClassVar[str] = ""    # `--<flag>` for the secret uuid
    default_provider_for_oauth: ClassVar[str] = ""

    # ---- argparse contribution -------------------------------------------

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Subclasses add their own flags (e.g. `--jira-secret-id`).

        Empty by default — many sources need no extra flags beyond what
        the scaffold already requires (`--owner`, `--repo`)."""

    # ---- shape builders --------------------------------------------------

    def build_source(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def build_tools(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> List[Dict[str, Any]]:
        """Default: source contributes no action tools. Override for
        trackers to expose comment/transition/etc. tools."""
        return []
