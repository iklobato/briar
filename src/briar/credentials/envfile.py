"""`EnvFileStore` — thin wrapper over process env vars (which is
where `/etc/briar/secrets.env` lands once systemd reads it via
``EnvironmentFile=``).

The only credential store of briar 2.0 (L3); there is no store interface
since spec 005 (G16). `briar auth login` writes through it, `briar auth
status` lists the names it holds."""

from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

from briar.credentials._paths import secrets_path as _secrets_path


log = logging.getLogger(__name__)


# Path resolution lives in `briar.credentials._paths` so the envfile
# bootstrap and the envfile store agree on a single source of truth.


_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Bytes that would corrupt the line-oriented envfile format on disk —
# a value containing `\n` would smuggle a second `KEY=value` line on
# the next bootstrap read, effectively letting one credential write
# inject another credential. NUL terminates C-string parsers in
# anything that reads the file via /etc/environment-style loaders.
_FORBIDDEN_VALUE_CHARS = ("\n", "\r", "\x00")


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(f"EnvFileStore: invalid env-var name {name!r} — must match {_NAME_RE.pattern}")


def _validate_value(value: str) -> None:
    """Reject control characters that would corrupt the on-disk
    envfile format. Specifically: newlines smuggle a second line;
    NUL truncates parsers."""
    for ch in _FORBIDDEN_VALUE_CHARS:
        if ch in value:
            raise ValueError(
                f"EnvFileStore: value contains disallowed control character {ch!r}; "
                "credentials with embedded newlines/NUL bytes can't be safely persisted "
                "to the line-oriented envfile format. Keep a multi-line secret in "
                "your shell environment instead."
            )


class LoginNames:
    """The env var names that count as briar credentials for ``list()``: the 2.0
    names `BRIAR_<COMPANY>_<SOURCE>_<FIELD>` of the login sources (spec 005, G7).
    Other `BRIAR_*` settings (`BRIAR_TELEMETRY`, `BRIAR_SECRETS_FILE`) do not match."""

    def pattern(self) -> "re.Pattern[str]":
        from briar.auth.logins import CompanyLogins  # local import: no cycle at module load

        sources = "|".join(source.upper() for source in CompanyLogins.SOURCES)
        return re.compile(f"^BRIAR_[A-Z0-9_]+_(?:{sources})_[A-Z_]+$")


class EnvFileStore:
    """Reads credentials from the process environment (where the startup bootstrap
    loaded `secrets.env`) and writes them to `secrets.env`."""

    def read(self, name: str) -> Optional[str]:
        return os.environ.get(name)

    def write(self, name: str, value: str) -> None:
        """Persist a credential to the secrets file atomically, then
        update ``os.environ`` for the running process.

        Atomicity: write to a sibling temp file opened with mode 0o600
        via ``os.open(... O_CREAT, 0o600)`` so the bytes are never
        readable by group/other (a previous ``write_text`` + ``chmod``
        sequence left a millisecond window of world-readable plaintext).
        Then ``os.replace`` swaps it in.

        Order: file first, env after. If the disk write fails the
        operator's process state is unchanged so a retry from
        ``briar auth login`` doesn't have a stale env to clean up.

        Idempotent: a line with the same KEY is replaced in-place;
        new keys are appended at the end.

        File-write failures raise ``OSError`` with a path-only message
        (the credential's env-var name is intentionally omitted so
        callers that log ``str(exc)`` don't leak which credential set
        is configured on this host)."""
        _validate_name(name)
        _validate_value(value)

        path = _secrets_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OSError(f"envfile-store: could not prepare {path}: {exc}") from exc

        try:
            existing = path.read_text() if path.exists() else ""
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

        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(updated)
            except Exception:
                # fdopen took ownership of fd; cleanup the partial temp file.
                try:
                    os.unlink(str(tmp))
                except OSError:
                    pass
                raise
            os.replace(str(tmp), str(path))
        except OSError as exc:
            raise OSError(f"envfile-store: could not persist credential to {path}: {exc}") from exc

        os.environ[name] = value

    def list(self) -> List[str]:
        pattern = LoginNames().pattern()
        return sorted(k for k in os.environ if pattern.match(k))
