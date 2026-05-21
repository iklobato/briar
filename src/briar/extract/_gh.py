"""GitHub helpers shared by the extractors.

`GithubApi` is a thin facade on top of PyGithub. The public surface
(`auth_token`, `get_json`, `get_paginated`, `client`) keeps the
extractor call sites unchanged — they pass GitHub API paths like
`/repos/{owner}/{repo}/pulls` and get back parsed JSON the same way as
the previous httpx-based implementation.

Backed by PyGithub instead of raw httpx so we get its battle-tested
retry / rate-limit / pagination / conditional-GET handling for free
instead of maintaining a parallel implementation.

Token resolution: `$GITHUB_TOKEN` only. The previous fallback that
shelled out to `gh auth token` was removed — briar is meant to run
headless (droplet, CI) where `gh` may not be installed, and the env
var is the universal contract."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Tuple

from briar.errors import CliError


log = logging.getLogger(__name__)


class GithubApi:
    """PyGithub-backed facade. The same `get_json` / `get_paginated`
    interface the extractors have always used, but no httpx, no manual
    pagination walking, no manual ETag cache — PyGithub's `Requester`
    layer does all of that internally."""

    BASE = "https://api.github.com"

    @staticmethod
    def auth_token() -> str:
        """Return $GITHUB_TOKEN, stripped, or empty string."""
        return os.environ.get("GITHUB_TOKEN", "").strip()

    @classmethod
    def _require_token(cls, token: str) -> str:
        tok = token or cls.auth_token()
        if not tok:
            raise CliError("GitHub credentials missing — set $GITHUB_TOKEN in /etc/briar/secrets.env or env.")
        return tok

    @classmethod
    def client(cls, token: str = ""):
        """Return a configured PyGithub `Github` client. Callers that
        want the high-level API (e.g. `client().get_repo(...)`)
        use this directly. The two convenience methods below build on
        the same client internally."""
        from github import Auth, Github

        tok = cls._require_token(token)
        return Github(auth=Auth.Token(tok), per_page=100, retry=3)

    @classmethod
    def get_json(cls, path: str, token: str = "") -> Any:
        """Single GET against api.github.com.

        Returns the parsed JSON payload. Tunnels through PyGithub's
        Requester so we benefit from its retry + rate-limit handling.
        """
        gh = cls.client(token)
        started = time.perf_counter()
        log.debug("gh GET path=%s", path)
        try:
            headers, payload = gh._Github__requester.requestJsonAndCheck("GET", path)
        except Exception:
            log.exception("gh GET error path=%s", path)
            raise
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        remaining = cls._extract_rate_remaining(headers, gh)
        log.info(
            "gh GET ok path=%s elapsed_ms=%d ratelimit_remaining=%s",
            path,
            elapsed_ms,
            remaining,
        )
        return payload

    @classmethod
    def get_paginated(cls, path: str, per_page: int = 100, max_pages: int = 50, token: str = "") -> List[Dict[str, Any]]:
        """Walk GitHub's Link-header pagination via PyGithub's Requester.

        PyGithub exposes pagination internally through the same low-level
        `requestJsonAndCheck`; we follow `Link: rel="next"` manually
        because the extractor call sites pass raw paths (not typed
        objects) and we need to return the raw JSON pages."""
        gh = cls.client(token)
        sep = "&" if "?" in path else "?"
        next_path: str = f"{path}{sep}per_page={per_page}"
        all_rows: List[Dict[str, Any]] = []
        visited = 0
        started = time.perf_counter()
        last_remaining = "?"
        log.debug("gh PAGINATED start path=%s per_page=%d max_pages=%d", path, per_page, max_pages)
        while next_path and visited < max_pages:
            try:
                headers, page = gh._Github__requester.requestJsonAndCheck("GET", next_path)
            except Exception:
                log.exception("gh PAGINATED error path=%s page=%d", path, visited + 1)
                raise
            last_remaining = cls._extract_rate_remaining(headers, gh) or last_remaining
            if isinstance(page, list):
                all_rows.extend(page)
            next_path = cls._next_link(headers.get("link", ""))
            visited += 1
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log.info(
            "gh PAGINATED ok path=%s pages=%d rows=%d elapsed_ms=%d ratelimit_remaining=%s",
            path,
            visited,
            len(all_rows),
            elapsed_ms,
            last_remaining,
        )
        if visited >= max_pages and next_path:
            log.warning("gh PAGINATED truncated path=%s hit_max_pages=%d (more pages exist)", path, max_pages)
        return all_rows

    @staticmethod
    def _next_link(link_header: str) -> str:
        """Parse the GitHub `Link` header for `rel="next"`. Returns the
        next-page path (with the leading `https://api.github.com`
        stripped — PyGithub's requester wants relative paths)."""
        if not link_header:
            return ""
        for chunk in link_header.split(","):
            if 'rel="next"' not in chunk:
                continue
            url_part = chunk.split(";", 1)[0].strip()
            if url_part.startswith("<") and url_part.endswith(">"):
                full = url_part[1:-1]
                if full.startswith(GithubApi.BASE):
                    return full[len(GithubApi.BASE) :]
                return full
        return ""

    @staticmethod
    def _extract_rate_remaining(response_headers: Dict[str, Any], gh) -> str:
        """PyGithub returns dict-like response headers from
        `requestJsonAndCheck`. The header name is lowercased by the
        underlying requests library. Fall back to the live rate-limit
        API if the header isn't present (rare — only for GraphQL or
        unauthenticated calls)."""
        for key in ("x-ratelimit-remaining", "X-RateLimit-Remaining"):
            if key in response_headers:
                return str(response_headers[key])
        try:
            rl = gh.get_rate_limit()
            return str(rl.core.remaining)
        except Exception:  # noqa: BLE001
            return "?"
