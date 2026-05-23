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


# Where the secrets file lives. Overridable via env var so tests and
# laptop installs can target a different path. systemd reads the same
# path on the droplet via EnvironmentFile=.
def _secrets_path() -> Path:
    return Path(os.environ.get("BRIAR_SECRETS_FILE") or "/etc/briar/secrets.env")


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
        """Update ``os.environ`` for the running process AND persist to
        the secrets file so the next process restart picks it up.

        Idempotent: an existing line with the same KEY is replaced
        in-place; a new key is appended at the end. Atomic via
        write-to-temp-then-rename — partial writes never leak.

        The secrets file is created with mode 0600 if missing. Parent
        directory is NOT auto-created — that should be an explicit
        deploy-time step (with the right ownership). If the file is
        unwritable, ``os.environ`` is still updated so the current
        process can proceed; the caller decides whether to surface the
        persistence failure."""
        _validate_name(name)
        os.environ[name] = value

        path = _secrets_path()
        if not path.exists():
            try:
                path.touch(mode=0o600)
            except OSError as exc:
                log.warning("envfile-store: could not create %s — value held in os.environ only: %s", path, exc)
                return

        new_line = f"{name}={value}\n"
        try:
            existing = path.read_text()
        except OSError as exc:
            log.warning("envfile-store: could not read %s — appending new value blindly: %s", path, exc)
            existing = ""

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
            log.warning("envfile-store: could not persist %s to %s — value held in os.environ only: %s", name, path, exc)

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
