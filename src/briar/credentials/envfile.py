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
from typing import List, Optional

from briar.credentials._paths import secrets_path as _secrets_path
from briar.credentials._store import CredentialStore


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
                "to the line-oriented envfile format. Use a different store (vault, "
                "infisical) if you genuinely need to persist a multi-line secret."
            )


# Env-var name MATCHERS that count as "credentials" for ``list()``.
# Derived from ``CredEnv`` at import time so adding a new credential
# enum entry can never silently fall out of the doctor's view (the
# previous hand-maintained list drifted — INFISICAL_* + FIREFLIES_*
# were missing from ``list()`` even though they exist in CredEnv).
#
# We match the FULL anchored template, NOT a bare prefix. A prefix match
# (``startswith``) over-matches the shared ``BRIAR_`` namespace:
# ``BRIAR_{c}_DATABASE_URL`` yields prefix ``BRIAR_``, which would sweep
# every config var (``BRIAR_SECRETS_FILE``, ``BRIAR_JOURNAL``, ...) into
# ``list()`` as if it were a credential. Matching
# ``^BRIAR_[A-Z0-9_]+_DATABASE_URL$`` keeps the DSN creds and leaves the
# config vars out.
#
# ``BRIAR_NOTIFY_SINKS`` is excluded — it's config (comma-separated
# sink kinds), not a credential, and listing it would be misleading.
_NON_CREDENTIAL_VARS = frozenset({"BRIAR_NOTIFY_SINKS"})


def _matcher_for(template: str) -> "re.Pattern[str]":
    """Compile a ``CredEnv`` template into an anchored regex. ``{c}`` —
    the per-company segment, which ``for_company`` upper-cases and
    underscore-normalises — becomes ``[A-Z0-9_]+``; a fixed name matches
    verbatim."""
    escaped = re.escape(template).replace(re.escape("{c}"), "[A-Z0-9_]+")
    return re.compile(f"^{escaped}$")


def _derive_known_matchers() -> tuple:
    from briar.env_vars import CredEnv  # local import to avoid cycle at module load

    return tuple(
        _matcher_for(member.value)
        for member in CredEnv
        if member.value not in _NON_CREDENTIAL_VARS
    )


_KNOWN_MATCHERS: tuple = _derive_known_matchers()


class EnvFileStore(CredentialStore):
    kind = "envfile"

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
                tmp = path.with_suffix(path.suffix + ".tmp")
                try:
                    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    try:
                        with os.fdopen(fd, "w") as f:
                            f.write(updated)
                    except Exception:
                        try:
                            os.unlink(str(tmp))
                        except OSError:
                            pass
                        raise
                    os.replace(str(tmp), str(path))
                except OSError as exc:
                    log.warning("envfile-store: could not persist deletion to %s: %s", path, exc)
            else:
                # Pattern matched in memory but file didn't actually have it;
                # nothing to persist. (e.g. duplicate key was already collapsed.)
                pass
        elif env_had:
            # File doesn't exist on disk but the in-memory env had it; only
            # the env-side deletion happened. Log so operators tailing logs
            # can tell apart "file never existed" from "file exists, no key
            # matched" — both look like "nothing to remove" from outside.
            log.info("envfile-store: secrets file %s does not exist; removed from os.environ only", path)

        return env_had or file_had

    def list(self) -> List[str]:
        return sorted(k for k in os.environ if any(m.match(k) for m in _KNOWN_MATCHERS))

    def expires_at(self, name: str) -> str:
        """AWS STS session tokens carry expiry inside the token itself,
        but parsing that requires the STS GetSessionToken API. For
        env-file creds we can't tell — return ``""`` and let the
        operator rotate based on the local SSO session timeout."""
        return ""
