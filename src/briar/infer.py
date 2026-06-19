"""Infer obvious values from the environment so they need not be typed.

Currently: the repo `owner`/`slug` from the local git checkout's `origin`
remote. Sits BELOW project config in the precedence chain — inference
only fills a flag that neither the CLI, env, nor config already provided.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# git@host:owner/repo(.git)  |  https://host/owner/repo(.git)
_SSH_RE = re.compile(r"^[^@]+@[^:]+:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")
_HTTP_RE = re.compile(r"^https?://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")


def git_remote_slug(cwd: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """`(owner, repo)` parsed from `git remote get-url origin`, or None
    when there is no git checkout / no origin / an unrecognised URL."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("infer: git remote lookup failed (%s)", exc)
        return None
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    match = _SSH_RE.match(url) or _HTTP_RE.match(url)
    if match is None:
        return None
    return match.group("owner"), match.group("repo")


def apply_inference_defaults(
    parser: argparse.ArgumentParser,
    already_satisfied: Optional[List[str]] = None,
    *,
    cwd: Optional[str] = None,
) -> List[str]:
    """Fill `--owner`/`--repo` from the git `origin` remote for actions a
    higher-precedence source (CLI/env/config) has not already satisfied.
    Mutates the matching actions' `default` + clears `required`; returns
    the dests that inference filled."""
    satisfied = set(already_satisfied or [])
    if "owner" in satisfied and "repo" in satisfied:
        return []
    slug = git_remote_slug(cwd)
    if slug is None:
        return []
    owner, repo = slug
    # Scalar dests get the bare name (agent/plan); an append --repo (the
    # canonical extract flag) gets the full owner/repo slug as a 1-list.
    scalar = {"owner": owner, "repo": repo}
    filled: List[str] = []
    from briar.config import _iter_subparsers

    for sub in _iter_subparsers(parser):
        for action in sub._actions:
            if action.dest not in scalar or action.dest in satisfied:
                continue
            if action.dest == "repo" and isinstance(action, argparse._AppendAction):
                action.default = [f"{owner}/{repo}"]
            else:
                action.default = scalar[action.dest]
            action.required = False
            filled.append(action.dest)
    return filled
