"""GitHub helpers shared by the extractors.

`GithubApi` exposes the three primitives the extractors need —
`auth_token`, `get_json`, `get_paginated`. It picks up the user's
existing `gh` CLI auth first, falling back to `$GITHUB_TOKEN`.

Static-only — no instance state worth carrying. Keeping the call sites
on `GithubApi.<method>` clusters all the GitHub-coupling under one
identifier and makes the file's purpose obvious from import lines."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

import httpx

from briar.errors import CliError


class GithubApi:
    BASE = "https://api.github.com"
    HEADERS = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    @staticmethod
    def auth_token() -> Optional[str]:
        """Try `gh auth token`, then `$GITHUB_TOKEN`, then None."""
        if shutil.which("gh"):
            proc = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        env_token = os.environ.get("GITHUB_TOKEN", "").strip()
        return env_token or None

    @classmethod
    def _require_token(cls, token: Optional[str]) -> str:
        tok = token or cls.auth_token()
        if not tok:
            raise CliError(
                "GitHub credentials missing — install `gh` + `gh auth login`, "
                "or set $GITHUB_TOKEN."
            )
        return tok

    @classmethod
    def _headers(cls, token: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {token}", **cls.HEADERS}

    @classmethod
    def get_json(cls, path: str, *, token: Optional[str] = None) -> Any:
        """Single GET against api.github.com."""
        tok = cls._require_token(token)
        response = httpx.get(
            f"{cls.BASE}{path}",
            headers=cls._headers(tok),
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

    @classmethod
    def get_paginated(
        cls,
        path: str,
        *,
        per_page: int = 100,
        max_pages: int = 50,
        token: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Walk GitHub's Link-header pagination to a hard ceiling."""
        tok = cls._require_token(token)
        sep = "&" if "?" in path else "?"
        url: Optional[str] = f"{cls.BASE}{path}{sep}per_page={per_page}"
        pages: List[Dict[str, Any]] = []
        visited = 0
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            while url and visited < max_pages:
                resp = client.get(url, headers=cls._headers(tok))
                resp.raise_for_status()
                page_data = resp.json()
                if type(page_data) is list:
                    pages.extend(page_data)
                url = cls._next_link(resp.headers.get("Link", ""))
                visited += 1
        return pages

    @staticmethod
    def _next_link(link_header: str) -> Optional[str]:
        """Parse the GitHub Link header for `rel="next"`."""
        for chunk in link_header.split(","):
            if 'rel="next"' not in chunk:
                continue
            url_part = chunk.split(";", 1)[0].strip()
            if url_part.startswith("<") and url_part.endswith(">"):
                return url_part[1:-1]
        return None
