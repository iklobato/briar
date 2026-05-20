"""Module-level constants — no runtime mutation, no `global`."""

from __future__ import annotations

from pathlib import Path
from typing import Final


DEFAULT_API_BASE: Final[str] = "https://api.usebriar.com"
CONFIG_DIR: Final[Path] = Path.home() / ".briar"
ACTIVE_FILE: Final[Path] = CONFIG_DIR / "active"
LEGACY_CONFIG_PATH: Final[Path] = CONFIG_DIR / "config.json"
DEFAULT_PROFILE: Final[str] = "default"

# WebSocket handshake — RFC 6455 §1.3 magic value.
WS_GUID: Final[str] = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Workspace header injected on every request — DRF picks this up.
WORKSPACE_HEADER: Final[str] = "X-Workspace-Id"

# Cookie names mirror the Next.js frontend's HttpOnly cookies, kept here
# for diagnostic reference (the CLI itself uses Bearer tokens, not cookies).
ACCESS_COOKIE: Final[str] = "briar_access"
REFRESH_COOKIE: Final[str] = "briar_refresh"
WORKSPACE_COOKIE: Final[str] = "briar_workspace"
