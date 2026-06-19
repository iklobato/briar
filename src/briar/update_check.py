"""Best-effort "a newer briar-cli is available" notice.

Throttled to one PyPI lookup per day (cached in the user cache dir), with
a short timeout, and entirely swallow-on-error: a failed or slow check
must never affect the command the user actually ran. Opt out via
``BRIAR_NO_UPDATE_CHECK=1``, ``DO_NOT_TRACK=1``, or ``BRIAR_TELEMETRY=off``.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger(__name__)

_PYPI_URL = "https://pypi.org/pypi/briar-cli/json"
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
_TIMEOUT_SECONDS = 2.0


def _opted_out() -> bool:
    if os.environ.get("BRIAR_NO_UPDATE_CHECK", "").strip().lower() in {"1", "true", "yes"}:
        return True
    if os.environ.get("DO_NOT_TRACK", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return os.environ.get("BRIAR_TELEMETRY", "").strip().lower() == "off"


def _state_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "briar" / "update_check.json"


def _read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, latest: str, now: float) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_check": now, "latest": latest}), encoding="utf-8")
    except OSError:
        pass  # cache is best-effort


def _fetch_latest() -> Optional[str]:
    try:
        with urllib.request.urlopen(_PYPI_URL, timeout=_TIMEOUT_SECONDS) as response:
            data = json.load(response)
        version = data.get("info", {}).get("version")
        return str(version) if version else None
    except Exception:  # noqa: BLE001 — network/parse failures are non-fatal
        log.debug("update-check: PyPI lookup failed", exc_info=True)
        return None


def _as_tuple(version: str) -> Optional[Tuple[int, ...]]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def _is_newer(latest: str, current: str) -> bool:
    latest_t, current_t = _as_tuple(latest), _as_tuple(current)
    if latest_t is None or current_t is None:
        return False
    return latest_t > current_t


def maybe_notify(current: str, *, now: Optional[float] = None) -> Optional[str]:
    """Return an upgrade notice if a newer briar-cli is on PyPI, else None.

    Uses the cached latest version when the last check was < 24h ago; only
    then does it hit the network (refreshing the cache). Never raises."""
    if _opted_out():
        return None
    stamp = now if now is not None else time.time()
    path = _state_path()
    state = _read_state(path)
    latest = state.get("latest")
    if not latest or (stamp - float(state.get("last_check", 0)) >= _CHECK_INTERVAL_SECONDS):
        fetched = _fetch_latest()
        if fetched:
            latest = fetched
            _write_state(path, latest, stamp)
    if latest and _is_newer(str(latest), current):
        return f"A new briar-cli is available: {current} -> {latest}. Upgrade: pip install -U briar-cli"
    return None
