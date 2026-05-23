"""`EnvFileStore` — thin wrapper over process env vars (which is
where `/etc/briar/secrets.env` lands once systemd reads it via
``EnvironmentFile=``).

This is the only `CredentialStore` backend that needs to work today.
It exposes the existing env-var surface through the new abstraction
so `briar secrets doctor` and any future store-backed code can use
one API regardless of where credentials live."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import List

from briar.credentials._store import CredentialStore


log = logging.getLogger(__name__)


# Where the secrets file lives. Resolution chain (first match wins):
#   1. $BRIAR_SECRETS_FILE       — explicit operator override
#   2. /etc/briar/secrets.env    — if already present (droplet convention,
#                                  systemd reads it via EnvironmentFile=)
#   3. $XDG_CONFIG_HOME/briar/secrets.env  (or ~/.config/briar/secrets.env)
#                                — laptop default (XDG-compliant)
# The chain "just works" on both deploy shapes without requiring the
# laptop user to set BRIAR_SECRETS_FILE manually.
def _secrets_path() -> Path:
    explicit = os.environ.get("BRIAR_SECRETS_FILE", "").strip()
    if explicit:
        return Path(explicit)
    system = Path("/etc/briar/secrets.env")
    if system.exists():
        return system
    base_str = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(base_str) if base_str else Path.home() / ".config"
    return base / "briar" / "secrets.env"


_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(f"EnvFileStore: invalid env-var name {name!r} — must match {_NAME_RE.pattern}")


# Canonical credential name prefixes — used by `list()` to enumerate
# which env vars are "credentials" vs unrelated process state.
# Updating CredEnv? Update this list too.
_KNOWN_PREFIXES: tuple = (
    "AWS_",
    "GITHUB_",
    "BITBUCKET_",
    "JIRA_",
    "LINEAR_",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "BRIAR_DATABASE_URL",
    "TELEGRAM_",
    "SLACK_",
    "SMTP_",
    "EMAIL_",
    "PAGERDUTY_",
)


class EnvFileStore(CredentialStore):
    kind = "envfile"

    def read(self, name: str) -> str:
        return os.environ.get(name, "")

    def write(self, name: str, value: str) -> None:
        """Update ``os.environ`` for the running process AND persist
        durably to the secrets file. Atomic via temp-file + rename.

        Idempotent: a line with the same KEY is replaced in-place;
        new keys are appended at the end.

        Parent directory is auto-created (``mkdir(parents=True,
        exist_ok=True)``) — the XDG default path
        (``~/.config/briar/``) doesn't exist on first use.

        File-write failures **raise** ``OSError`` AFTER ``os.environ``
        has been updated. Two reasons: (1) the calling process still
        benefits from the in-memory update; (2) the caller (typically
        ``briar auth login``) needs the exception so the per-key
        ok/FAIL summary reflects what was actually durable. Previous
        behaviour was to swallow the error and lie — fixed."""
        _validate_name(name)
        os.environ[name] = value

        path = _secrets_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.touch(mode=0o600)
        except OSError as exc:
            raise OSError(f"envfile-store: could not prepare {path}: {exc}") from exc

        try:
            existing = path.read_text()
        except OSError as exc:
            raise OSError(f"envfile-store: could not read {path}: {exc}") from exc

        new_line = f"{name}={value}\n"
        # Match `KEY=value` (possibly with leading whitespace, possibly
        # `export KEY=value`) so we replace in place rather than appending
        # a duplicate that shadows the first one.
        pattern = re.compile(rf"^(?:export\s+)?{re.escape(name)}=.*\n?", re.MULTILINE)
        if pattern.search(existing):
            updated = pattern.sub(new_line, existing, count=1)
        else:
            tail = "" if existing.endswith("\n") or not existing else "\n"
            updated = existing + tail + new_line

        try:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(updated)
            os.chmod(tmp, 0o600)
            tmp.replace(path)
        except OSError as exc:
            raise OSError(f"envfile-store: could not persist {name} to {path}: {exc}") from exc

    def delete(self, name: str) -> bool:
        """Remove from both ``os.environ`` and the persisted file.
        Returns True iff something was actually removed (from either
        location). Logout-friendly: if neither has the value, return
        False so the CLI can report "nothing to do"."""
        _validate_name(name)
        env_had = name in os.environ
        if env_had:
            del os.environ[name]

        path = _secrets_path()
        file_had = False
        if path.exists():
            try:
                existing = path.read_text()
            except OSError:
                return env_had
            pattern = re.compile(rf"^(?:export\s+)?{re.escape(name)}=.*\n?", re.MULTILINE)
            if pattern.search(existing):
                file_had = True
                updated = pattern.sub("", existing, count=1)
                try:
                    tmp = path.with_suffix(path.suffix + ".tmp")
                    tmp.write_text(updated)
                    os.chmod(tmp, 0o600)
                    tmp.replace(path)
                except OSError as exc:
                    log.warning("envfile-store: could not persist deletion of %s: %s", name, exc)

        return env_had or file_had

    def list(self) -> List[str]:
        return sorted(k for k in os.environ if any(k.startswith(p) for p in _KNOWN_PREFIXES))

    def expires_at(self, name: str) -> str:
        """AWS STS session tokens carry expiry inside the token itself,
        but parsing that requires the STS GetSessionToken API. For
        env-file creds we can't tell — return ``""`` and let the
        operator rotate based on the local SSO session timeout."""
        return ""
