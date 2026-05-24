"""briar — terminal client for the Briar agent-orchestration API.

Stdlib-only. Importing the top-level package exposes only the version;
public surfaces live in `briar.cli` (entry point) and the typed
sub-packages (`briar.commands`, `briar.iac`, `briar.formatting`, etc.).

The version string is read from package metadata at import time so it
stays in lockstep with whatever the installed wheel was built from. A
hardcoded literal here drifts every time the auto-release CI bumps
pyproject.toml — `importlib.metadata` reads the same value, no
manual sync."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version


try:
    __version__ = _pkg_version("briar-cli")
except PackageNotFoundError:
    # Source-tree checkout with no installed metadata (rare — `pip
    # install -e .` registers metadata, but a bare `python -m briar`
    # against an unbuilt tree wouldn't). Fall back to a sentinel that
    # makes the drift obvious instead of pretending we know.
    __version__ = "0.0.0+unknown"
