"""Envfile `CredentialBootstrap`.

Reads the persisted ``secrets.env`` file at process startup and writes
each ``KEY=value`` line into ``os.environ`` via ``setdefault`` (so an
already-set env var — from the shell, systemd ``EnvironmentFile=``,
or a higher-precedence bootstrap — always wins).

On a droplet, systemd's ``EnvironmentFile=/etc/briar/secrets.env``
already populates the process env before briar starts, so this
bootstrap finds every key already set and reports them all as
``skipped`` (no-op, as intended). On a laptop, the same file lives
at ``$XDG_CONFIG_HOME/briar/secrets.env`` and nothing else loads
it — that's where this bootstrap earns its keep, picking up
credentials the operator persisted via
``briar auth login --store envfile``.

Pairs with ``InfisicalBootstrap`` as a cascade: envfile runs first
(local, cheap, no network), Infisical runs second and fills in
anything envfile didn't have. If Infisical's machine-identity
credentials 401, envfile values survive — the operator can still
work against whatever they've already logged into locally."""

from __future__ import annotations

import logging
import os
import re
from typing import List

from briar.credentials._bootstrap import CredentialBootstrap, HydrateResult
from briar.credentials._paths import secrets_path as _resolve_secrets_path


log = logging.getLogger(__name__)


# `KEY=value` with an optional `export ` prefix, leading whitespace,
# and a trailing `# comment`. Captures whatever follows `=` verbatim
# so quoted values (containing `#`) survive without being truncated.
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


# Env vars the dynamic-loader / interpreter / proxy stack reads at
# process startup. An envfile is operator-owned but its contents can
# come from a managed vault that operators don't control directly
# (Infisical project, encrypted blob in CI). Refuse to hydrate these
# from disk so a compromised vault entry can't smuggle code-execution
# (LD_PRELOAD, PYTHONPATH) or traffic-interception (HTTP_PROXY) into
# the briar process. Operators that genuinely need these can set them
# in the shell or systemd unit.
_DENY_ENV_VARS: frozenset = frozenset(
    {
        # Dynamic linker (Linux/glibc + macOS variants)
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        # Python interpreter knobs
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "PYTHONINSPECT",
        # System PATH
        "PATH",
        # Outbound HTTP interception
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        # SSL trust override (could disable verification globally)
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    }
)


def _strip_quotes(value: str) -> str:
    """Mimic /bin/sh's quote handling for envfile values: strip ONE
    matched pair of surrounding single OR double quotes, leave inner
    content alone. ``FOO="bar"`` → ``bar``; ``FOO='b"a"r'`` → ``b"a"r``;
    ``FOO=bar`` → ``bar``."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


class EnvFileBootstrap(CredentialBootstrap):
    """Hydrates ``os.environ`` from the persisted ``secrets.env`` file.

    ``is_available()`` checks file existence — no remote dependency.
    ``hydrate()`` returns a structured ``HydrateResult`` even on a
    read failure (matches the InfisicalBootstrap shape, so startup
    never crashes on a misconfigured envfile)."""

    kind = "envfile"

    def __init__(self) -> None:
        # Defer path resolution to call sites so tests that swap
        # BRIAR_SECRETS_FILE between is_available() and hydrate()
        # see the swapped value.
        pass

    def is_available(self) -> bool:
        return _resolve_secrets_path().exists()

    def hydrate(self, *, dry_run: bool = False) -> HydrateResult:
        path = _resolve_secrets_path()
        if not path.exists():
            return HydrateResult(backend=self.kind, error=f"no envfile at {path}")

        try:
            content = path.read_text()
        except OSError as exc:
            log.warning("envfile-bootstrap: read failed: %s", exc)
            return HydrateResult(backend=self.kind, error=f"read failed: {exc}")

        written: List[str] = []
        skipped: List[str] = []
        for raw_line in content.splitlines():
            # Drop blank lines + comments. A `#` mid-line is part of
            # the value (no shell-style trailing-comment stripping —
            # `briar auth login` never writes one, and we don't want
            # to surprise an operator who hand-edited their envfile
            # with a value that happens to contain `#`).
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _LINE_RE.match(line)
            if not match:
                log.debug("envfile-bootstrap: skipping malformed line: %r", raw_line)
                continue
            key, value = match.group(1), _strip_quotes(match.group(2))
            if key in _DENY_ENV_VARS:
                # A managed-vault entry trying to set LD_PRELOAD etc.
                # is either an operator mistake or an injection attempt
                # — either way, surface it loudly and skip.
                log.warning(
                    "envfile-bootstrap: refusing to hydrate disallowed env var %s "
                    "(loader/interpreter/proxy var — set via shell or systemd if genuinely needed)",
                    key,
                )
                continue
            if key in os.environ:
                skipped.append(key)
                continue
            if not dry_run:
                os.environ[key] = value
            written.append(key)

        log.info(
            "envfile-bootstrap: backend=%s wrote=%d preserved=%d path=%s%s",
            self.kind,
            len(written),
            len(skipped),
            path,
            " (DRY RUN — nothing written)" if dry_run else "",
        )
        return HydrateResult(backend=self.kind, written=written, skipped=skipped)

    @classmethod
    def required_env_vars(cls) -> List[str]:
        # The envfile bootstrap doesn't need machine-identity creds —
        # it's just a local file read. `briar secrets doctor` reports
        # no requirements, which is correct.
        return []
