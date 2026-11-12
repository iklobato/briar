"""Environment health checks for `briar doctor`.

Lightweight and offline: inspects the Python/briar version, project config,
git inference, and the presence of the credentials the common flows need.
No network, no store read: just "is this machine set up to run
briar?". Each check is a `Check`; the command renders them and exits
non-zero if any is a hard ``fail``.
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from typing import List

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _python_check() -> Check:
    v = platform.python_version()
    ok = sys.version_info >= (3, 10)
    return Check("python", OK if ok else FAIL, f"{v}{'' if ok else ' (briar needs >= 3.10)'}")


def _version_check() -> Check:
    from briar import __version__

    return Check("briar", OK, __version__)


def _config_check() -> Check:
    from briar.config import find_config_file

    path = find_config_file()
    if path is None:
        return Check("project config", WARN, "no .briar.toml found: run `briar init`")
    return Check("project config", OK, str(path))


def _git_check() -> Check:
    from briar.infer import git_remote_slug

    slug = git_remote_slug()
    if slug is None:
        return Check("git remote", WARN, "no git origin: run `briar init --owner OWNER --repo REPO`, or set [repo] in .briar.toml")
    return Check("git remote", OK, f"{slug[0]}/{slug[1]} (inferred)")


def _env_present(*names: str) -> bool:
    return any(os.environ.get(n) for n in names)


def _github_check() -> Check:
    if _env_present("GITHUB_TOKEN"):
        return Check("github token", OK, "GITHUB_TOKEN set")
    return Check("github token", WARN, "GITHUB_TOKEN not set: the GitHub extractors need it")


_CHECKS = (
    _python_check,
    _version_check,
    _config_check,
    _git_check,
    _github_check,
)


def run_checks() -> List[Check]:
    """Run every health check in display order."""
    return [check() for check in _CHECKS]


def worst_status(checks: List[Check]) -> str:
    """The most severe status across `checks` (fail > warn > ok)."""
    statuses = {c.status for c in checks}
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return OK
