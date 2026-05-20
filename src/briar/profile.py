"""Profile resolution + filesystem layout.

Each profile is an isolated credential bundle stored at
`~/.briar/<name>/config.json`. Selection priority:
  1. explicit `--profile` flag
  2. `BRIAR_PROFILE` env var
  3. `~/.briar/active` file (written by `briar profile use`)
  4. literal "default"

Legacy `~/.briar/config.json` (pre-profile installs) is migrated into
`~/.briar/default/config.json` on first import.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from briar.settings import (
    ACTIVE_FILE,
    CONFIG_DIR,
    DEFAULT_PROFILE,
    LEGACY_CONFIG_PATH,
)


def resolve_profile(cli_value: Optional[str]) -> str:
    """Pick the active profile name without consulting `getattr` /
    `global`; pure dict-style lookup chain implemented as early
    returns."""
    if cli_value:
        return cli_value
    env_value = os.environ.get("BRIAR_PROFILE", "").strip()
    if env_value:
        return env_value
    try:
        active = ACTIVE_FILE.read_text().strip()
    except FileNotFoundError:
        return DEFAULT_PROFILE
    return active or DEFAULT_PROFILE


def config_path_for(profile: str) -> Path:
    return CONFIG_DIR / profile / "config.json"


def list_profiles() -> List[str]:
    """Profiles with a non-empty `config.json` under `~/.briar/`."""
    if not CONFIG_DIR.exists():
        return []
    out: List[str] = []
    for entry in sorted(CONFIG_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / "config.json").exists():
            out.append(entry.name)
    return out


def migrate_legacy_config_if_present() -> None:
    """One-shot move of the pre-profile `~/.briar/config.json` into the
    `default` profile slot. Safe to call repeatedly."""
    if not LEGACY_CONFIG_PATH.exists():
        return
    target = config_path_for(DEFAULT_PROFILE)
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(LEGACY_CONFIG_PATH.read_text())
    LEGACY_CONFIG_PATH.unlink()
