"""GitHub helpers shared by the extractors.

`GithubApi` exposes three primitives — `auth_token`, `get_json`,
`get_paginated`. Picks up the user's existing `gh` CLI auth first,
falling back to `$GITHUB_TOKEN`. Empty-string return = "no token";
callers check truthiness, never identity."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List

import httpx

from briar.errors import CliError


log = logging.getLogger(__name__)


class GithubApi:
    BASE = "https://api.github.com"
    HEADERS = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    @staticmethod
    def auth_token() -> str:
        """Try `gh auth token`, then `$GITHUB_TOKEN`, then `""`."""
        if shutil.which("gh"):
            proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        return os.environ.get("GITHUB_TOKEN", "").strip()

    @classmethod
    def _require_token(cls, token: str) -> str:
        tok = token or cls.auth_token()
        if not tok:
            raise CliError("GitHub credentials missing — install `gh` + `gh auth login`, or set $GITHUB_TOKEN.")
        return tok

    @classmethod
    def _headers(cls, token: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {token}", **cls.HEADERS}

    @classmethod
    def get_json(cls, path: str, token: str = "") -> Any:
        """Single GET against api.github.com."""
        tok = cls._require_token(token)
        url = f"{cls.BASE}{path}"
        log.debug("gh GET path=%s", path)
        started = time.perf_counter()
        try:
            response = httpx.get(url, headers=cls._headers(tok), timeout=30.0)
        except httpx.HTTPError:
            log.exception("gh GET network error path=%s", path)
            raise
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if not response.is_success:
            log.error(
                "gh GET non-2xx path=%s status=%s elapsed_ms=%d ratelimit_remaining=%s body_preview=%r",
                path,
                response.status_code,
                elapsed_ms,
                response.headers.get("x-ratelimit-remaining", "?"),
                response.text[:200],
            )
            response.raise_for_status()
        log.debug("gh GET ok path=%s elapsed_ms=%d", path, elapsed_ms)
        return response.json()

    @classmethod
    def get_paginated(cls, path: str, per_page: int = 100, max_pages: int = 50, token: str = "") -> List[Dict[str, Any]]:
        """Walk GitHub's Link-header pagination to a hard ceiling."""
        tok = cls._require_token(token)
        sep = "&" if "?" in path else "?"
        url = f"{cls.BASE}{path}{sep}per_page={per_page}"
        pages: List[Dict[str, Any]] = []
        visited = 0
        started = time.perf_counter()
        log.debug("gh PAGINATED start path=%s per_page=%d max_pages=%d", path, per_page, max_pages)
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            while url and visited < max_pages:
                try:
                    resp = client.get(url, headers=cls._headers(tok))
                except httpx.HTTPError:
                    log.exception("gh PAGINATED network error path=%s page=%d", path, visited + 1)
                    raise
                if not resp.is_success:
                    log.error(
                        "gh PAGINATED non-2xx path=%s page=%d status=%s ratelimit_remaining=%s body_preview=%r",
                        path,
                        visited + 1,
                        resp.status_code,
                        resp.headers.get("x-ratelimit-remaining", "?"),
                        resp.text[:200],
                    )
                    resp.raise_for_status()
                page_data = resp.json()
                if type(page_data) is list:
                    pages.extend(page_data)
                url = cls._next_link(resp.headers.get("Link", ""))
                visited += 1
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log.debug("gh PAGINATED ok path=%s pages=%d rows=%d elapsed_ms=%d", path, visited, len(pages), elapsed_ms)
        if visited >= max_pages and url:
            log.warning("gh PAGINATED truncated path=%s hit_max_pages=%d (more pages exist)", path, max_pages)
        return pages

    @staticmethod
    def _next_link(link_header: str) -> str:
        """Parse the GitHub `Link` header for `rel="next"`. `""` if none."""
        for chunk in link_header.split(","):
            if 'rel="next"' not in chunk:
                continue
            url_part = chunk.split(";", 1)[0].strip()
            if url_part.startswith("<") and url_part.endswith(">"):
                return url_part[1:-1]
        return ""
