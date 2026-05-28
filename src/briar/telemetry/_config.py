"""Telemetry configuration — resolution, install-id, banner state.

Precedence (highest first):
1. `DO_NOT_TRACK=1` env var — industry standard, wins over everything.
2. `BRIAR_TELEMETRY` env var — `off`, `errors-only`, `full`.
3. Persisted config at `<XDG_CONFIG_HOME>/briar/telemetry.json` — set by
   `briar telemetry off / errors-only / full`.
4. Default: `full` (errors + usage).

The install_id is a stable random UUID generated on first run, stored at
`<XDG_CONFIG_HOME>/briar/install_id`. It NEVER includes hostname,
username, IP, or any other identifying bit — it's purely a de-dup key
so we can count distinct installs without ever knowing who you are.
When we ship the id to Sentry we send the SHA256 prefix, not the raw
UUID, so even our own Sentry project can't link back to your home dir."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

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
    banner_shown: bool = False
    source: str = ""  # "env" / "do-not-track" / "config-file" / "default"

    @property
    def enabled(self) -> bool:
        return self.tier is not TelemetryTier.OFF

    @property
    def hashed_install_id(self) -> str:
        """SHA256 prefix of the install_id — what we actually send to
        Sentry. The raw id never leaves the local machine."""
        return hashlib.sha256(self.install_id.encode("utf-8")).hexdigest()[:16]


def resolve(env: Optional[dict] = None) -> TelemetryConfig:
    """Resolve the effective config. `env` defaults to `os.environ` —
    overridable for tests."""
    env = env if env is not None else dict(os.environ)
    if env.get("DO_NOT_TRACK", "").strip() == "1":
        return _build_config(TelemetryTier.OFF, source="do-not-track")

    env_tier = (env.get("BRIAR_TELEMETRY") or "").strip().lower()
    if env_tier:
        try:
            return _build_config(TelemetryTier(env_tier), source="env")
        except ValueError:
            log.warning("telemetry: BRIAR_TELEMETRY=%r is not a valid tier; ignoring", env_tier)

    saved = _read_saved_config()
    if saved is not None:
        tier_str = saved.get("tier") or ""
        try:
            return _build_config(
                TelemetryTier(tier_str),
                source="config-file",
                banner_shown=bool(saved.get("banner_shown")),
            )
        except ValueError:
            log.warning("telemetry: saved tier %r invalid; falling back to default", tier_str)

    return _build_config(TelemetryTier.FULL, source="default")


def _build_config(tier: TelemetryTier, *, source: str, banner_shown: bool = False) -> TelemetryConfig:
    return TelemetryConfig(
        tier=tier,
        install_id=_load_or_create_install_id(),
        dsn=os.environ.get("BRIAR_SENTRY_DSN") or _DEFAULT_DSN,
        banner_shown=banner_shown,
        source=source,
    )


# ─── persistence ────────────────────────────────────────────────────


def _telemetry_state_path() -> Path:
    return _config_dir() / "telemetry.json"


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


def _read_saved_config() -> Optional[dict]:
    path = _telemetry_state_path()
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("telemetry: saved config at %s unreadable: %s", path, exc)
        return None


def save_tier(tier: TelemetryTier, *, banner_shown: bool = True) -> Path:
    """Write the chosen tier to the on-disk state file. Called by the
    `briar telemetry` subcommand and by the first-run banner."""
    path = _telemetry_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tier": tier.value, "banner_shown": banner_shown}
    path.write_text(json.dumps(payload, indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def mark_banner_shown() -> None:
    """Persist that the first-run banner has been displayed. Idempotent."""
    cfg = resolve()
    save_tier(cfg.tier, banner_shown=True)


def reset_install_id() -> str:
    """Regenerate the install_id (called by `briar telemetry reset`).
    Returns the new id."""
    global _INSTALL_ID_CACHE
    path = _install_id_path()
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
    # Invalidate the in-memory cache BEFORE regenerating. Without this,
    # `_load_or_create_install_id()` returns the value `resolve()` already
    # cached this process — so `reset` deleted the file but handed back the
    # OLD id and never persisted a new one (a no-op rotation in any process
    # that touched telemetry first).
    _INSTALL_ID_CACHE = None
    return _load_or_create_install_id()


# Exposed paths for the `briar telemetry status` subcommand. Tests
# can monkey-patch by setting XDG_CONFIG_HOME.
config_dir = _config_dir
state_path = _telemetry_state_path
install_id_path = _install_id_path


def default_dsn() -> str:
    return _DEFAULT_DSN
