"""Telemetry configuration — resolution and install-id.

Precedence (highest first):
1. `DO_NOT_TRACK=1` env var — industry standard, wins over everything.
2. `BRIAR_TELEMETRY` env var — `off`, `errors-only`, `full`.
3. `telemetry = "<tier>"` in the project config (`.briar.toml`, or
   `[tool.briar]` in `pyproject.toml`). Decision C7: there is no
   `briar telemetry` command and no state file in 2.0. The 1.x file
   `telemetry.json` is never read: 1.x saved `full` for everyone who saw
   its first-run banner, so it holds no real choice.
4. Default: `off`. Telemetry is opt-in since 2.0.

The install_id is a stable random UUID generated on first run, stored at
`<XDG_CONFIG_HOME>/briar/install_id`. It NEVER includes hostname,
username, IP, or any other identifying bit — it's purely a de-dup key
so we can count distinct installs without ever knowing who you are.
When we ship the id to Sentry we send the SHA256 prefix, not the raw
UUID, so even our own Sentry project can't link back to your home dir."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional

log = logging.getLogger(__name__)


# Sentry DSN for the `iklobato/briar-cli` project. DSNs are public-by-
# design — committing this to git is intentional and matches the OSS
# CLI pattern (gh, homebrew, vscode-cli all do the same). Override per-
# host or per-org with `BRIAR_SENTRY_DSN`. Empty string would no-op the
# sink; we keep the real DSN so 1.1.15+ ships telemetry working out of
# the box (subject to the user's opt-out tier).
_DEFAULT_DSN = "https://73d63cbb5b2c3465b68b39464493b7cb@o4505128597127168.ingest.us.sentry.io/4511451799617536"


class TelemetryTier(str, Enum):
    """Closed lifecycle of telemetry collection. Adding a tier is a
    deliberate schema bump."""

    OFF = "off"
    ERRORS_ONLY = "errors-only"
    FULL = "full"


def _config_dir() -> Path:
    """`$XDG_CONFIG_HOME/briar` (Linux) or `~/.config/briar` everywhere
    else. Created on demand."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "briar"


@dataclass
class TelemetryConfig:
    """Resolved configuration for one process. Built by `resolve()`."""

    tier: TelemetryTier
    install_id: str
    dsn: str = ""
    source: str = ""  # "env" / "do-not-track" / "project-config" / "default"

    @property
    def enabled(self) -> bool:
        return self.tier is not TelemetryTier.OFF

    @property
    def hashed_install_id(self) -> str:
        """SHA256 prefix of the install_id — what we actually send to
        Sentry. The raw id never leaves the local machine."""
        return hashlib.sha256(self.install_id.encode("utf-8")).hexdigest()[:16]


def resolve(env: Optional[dict] = None, project_config: Optional[Mapping[str, object]] = None) -> TelemetryConfig:
    """Resolve the effective config. `env` defaults to `os.environ` —
    overridable for tests. `project_config` is the already-read project
    config section (`briar.config.load_project_config`), or None."""
    env = env if env is not None else dict(os.environ)
    if env.get("DO_NOT_TRACK", "").strip() == "1":
        return _build_config(TelemetryTier.OFF, source="do-not-track")

    env_tier = (env.get("BRIAR_TELEMETRY") or "").strip().lower()
    if env_tier:
        try:
            return _build_config(TelemetryTier(env_tier), source="env")
        except ValueError:
            log.warning("telemetry: BRIAR_TELEMETRY=%r is not a valid tier; ignoring", env_tier)

    file_tier = str((project_config or {}).get("telemetry") or "").strip().lower()
    if file_tier:
        try:
            return _build_config(TelemetryTier(file_tier), source="project-config")
        except ValueError:
            log.warning("telemetry: telemetry=%r in the project config is not a valid tier; ignoring", file_tier)

    return _build_config(TelemetryTier.OFF, source="default")


def _build_config(tier: TelemetryTier, *, source: str) -> TelemetryConfig:
    return TelemetryConfig(
        tier=tier,
        install_id=_load_or_create_install_id(),
        dsn=os.environ.get("BRIAR_SENTRY_DSN") or _DEFAULT_DSN,
        source=source,
    )


# ─── persistence ────────────────────────────────────────────────────


def _install_id_path() -> Path:
    return _config_dir() / "install_id"


# Module-level cache for the install-id. Without this, a read-only
# home dir would force every call to `resolve()` to generate a fresh
# UUID, inflating the "distinct installs" metric in Sentry. We cache
# in-memory so subsequent reads within one process return the same id
# even when persistence failed.
_INSTALL_ID_CACHE: Optional[str] = None


def _load_or_create_install_id() -> str:
    """Read the persisted install_id, or generate + persist a fresh one.

    Failure to read/write is non-fatal — we generate a one-shot id
    in-memory so the rest of telemetry still works. The first generated
    id is cached so subsequent calls within the process get the same
    value even on read-only home directories (CI, containers)."""
    global _INSTALL_ID_CACHE
    if _INSTALL_ID_CACHE is not None:
        return _INSTALL_ID_CACHE
    path = _install_id_path()
    try:
        loaded = path.read_text().strip()
    except FileNotFoundError:
        loaded = ""
    except OSError:
        # Generate once + cache so distinct-installs metric stays stable
        # for the process lifetime even if disk is unwritable.
        _INSTALL_ID_CACHE = uuid.uuid4().hex
        return _INSTALL_ID_CACHE
    _INSTALL_ID_CACHE = loaded or _persist_new_install_id(path)
    return _INSTALL_ID_CACHE


def _persist_new_install_id(path: Path) -> str:
    new_id = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_id)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        log.debug("telemetry: could not persist install_id at %s", path)
    return new_id
