"""GitHub helpers shared by the extractors.

Uses `gh auth token` (preferred — picks up the user's existing CLI
auth) with $GITHUB_TOKEN as a fallback. All requests go through httpx
with the same retry policy as the rest of the CLI."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

import httpx

from briar.errors import CliError


_GH_API = "https://api.github.com"


def auth_token() -> Optional[str]:
    """Try `gh auth token`, then $GITHUB_TOKEN, then None."""
    if shutil.which("gh"):
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    env_token = os.environ.get("GITHUB_TOKEN", "").strip()
    return env_token or None


def get_json(path: str, *, token: Optional[str] = None) -> Any:
    """Single GET against api.github.com."""
    tok = token or auth_token()
    if not tok:
        raise CliError(
            "GitHub credentials missing — install `gh` + `gh auth login`, "
            "or set $GITHUB_TOKEN."
        )
    response = httpx.get(
        f"{_GH_API}{path}",
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def get_paginated(
    path: str,
    *,
    per_page: int = 100,
    max_pages: int = 50,
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Walk GitHub's Link-header pagination to a hard ceiling."""
    tok = token or auth_token()
    if not tok:
        raise CliError("GitHub credentials missing.")
    sep = "&" if "?" in path else "?"
    url: Optional[str] = f"{_GH_API}{path}{sep}per_page={per_page}"
    pages: List[Dict[str, Any]] = []
    visited = 0
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        while url and visited < max_pages:
            resp = client.get(
                url,
                headers={
                    "Authorization": f"Bearer {tok}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            resp.raise_for_status()
            page_data = resp.json()
            if type(page_data) is list:
                pages.extend(page_data)
            url = _next_link(resp.headers.get("Link", ""))
            visited += 1
    return pages


def _next_link(link_header: str) -> Optional[str]:
    """Parse the GitHub Link header for `rel="next"`."""
    for chunk in link_header.split(","):
        if 'rel="next"' not in chunk:
            continue
        url_part = chunk.split(";", 1)[0].strip()
        if url_part.startswith("<") and url_part.endswith(">"):
            return url_part[1:-1]
    return None
