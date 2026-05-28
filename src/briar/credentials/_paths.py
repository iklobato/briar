"""Filesystem-path helpers shared between the envfile store and the
envfile bootstrap.

Single source of truth for `secrets_path()` — previously inlined in
both `envfile.py` (the store) and `_bootstraps/envfile.py` (the
bootstrap). The cycle that justified the duplication was avoidable:
this module imports nothing from `briar.credentials` itself, so it
can be safely imported by both consumers.

Resolution chain (first match wins):
  1. ``$BRIAR_SECRETS_FILE`` — explicit operator override
  2. ``/etc/briar/secrets.env`` — droplet convention (systemd
     ``EnvironmentFile=`` reads it before briar starts)
  3. ``$XDG_CONFIG_HOME/briar/secrets.env`` (or
     ``~/.config/briar/secrets.env``) — laptop default, XDG-compliant.
"""

from __future__ import annotations

import os
from pathlib import Path


_SYSTEM_PATH = Path("/etc/briar/secrets.env")


def secrets_path() -> Path:
    """Resolve the active secrets file path. See module docstring for
    the precedence chain."""
    explicit = os.environ.get("BRIAR_SECRETS_FILE", "").strip()
    if explicit:
        return Path(explicit)
    if _SYSTEM_PATH.exists():
        return _SYSTEM_PATH
    base_str = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(base_str) if base_str else Path.home() / ".config"
    return base / "briar" / "secrets.env"
