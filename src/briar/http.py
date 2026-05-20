"""HTTP seam between commands and the Briar API.

Built on `httpx` — adds JWT refresh on 401, X-Workspace-Id injection,
and DRF pagination walking. The `Client` instance pools connections,
respects `follow_redirects` (Next-style 308-on-trailing-slash works),
and uses HTTP/1.1 by default (HTTP/2 available if the server supports
it)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from briar.credentials import Credentials, CredentialsStore
from briar.errors import ApiError, AuthError, CliError
from briar.pagination import items_of, next_of, to_relative
from briar.settings import WORKSPACE_HEADER


_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)

# Backoff schedule for transient 5xx (e.g. backend DB pool exhaustion).
# Sequence of sleep seconds between attempts. Total ~30s ceiling.
_RETRY_DELAYS_5XX: Tuple[float, ...] = (1.0, 3.0, 8.0, 18.0)


def _decode(response: httpx.Response) -> Any:
    """Return parsed JSON, falling back to raw text. Empty body → None."""
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


class ApiClient:
    """Adapter around `httpx.Client`. Single seam used by every command.

    Why a class: it's the *only* place that touches the network, knows
    about JWT refresh, or walks pagination. Tests stub it directly."""

    def __init__(self, store: CredentialsStore) -> None:
        self._store = store
        self._http = httpx.Client(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "briar-cli/0.4"},
        )

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ---- accessors -------------------------------------------------------

    @property
    def store(self) -> CredentialsStore:
        return self._store

    @property
    def creds(self) -> Credentials:
        return self._store.creds

    # ---- public verbs ----------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        body: Optional[Any] = None,
        query: Optional[Dict[str, Any]] = None,
    ) -> Any:
        status, payload = self._send_with_retry(method, path, body, query)

        retryable_401 = (
            status == 401
            and bool(self.creds.refresh)
            and "/auth/token" not in path
        )
        if retryable_401:
            self._refresh_token()
            status, payload = self._send_with_retry(method, path, body, query)

        if 200 <= status < 300:
            return payload
        raise ApiError(status, payload, method, path)

    def _send_with_retry(
        self,
        method: str,
        path: str,
        body: Optional[Any],
        query: Optional[Dict[str, Any]],
    ) -> Tuple[int, Any]:
        """Wrap `_send` with exponential backoff on 5xx responses.

        Transient backend errors (DB pool exhaustion, slow restarts)
        return 5xx with HTML bodies; a short retry sequence usually
        rides them out. 4xx is *not* retried — those are client-side
        bugs that won't fix themselves."""
        attempt = 0
        last_status = 0
        last_payload: Any = None
        while True:
            status, payload = self._send(method, path, body, query)
            if status < 500 or attempt >= len(_RETRY_DELAYS_5XX):
                return status, payload
            last_status, last_payload = status, payload
            time.sleep(_RETRY_DELAYS_5XX[attempt])
            attempt += 1

    def list_all(
        self,
        path: str,
        query: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Walk DRF pagination to exhaustion."""
        out: List[Dict[str, Any]] = []
        next_url: Optional[str] = path
        params: Optional[Dict[str, Any]] = query
        while next_url:
            page = self.request("GET", next_url, None, params)
            params = None  # only the first hop carries our params
            out.extend(items_of(page))
            following = next_of(page)
            if not following:
                return out
            next_url = to_relative(following, self.creds.api_base)
        return out

    # ---- internals -------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        out: Dict[str, str] = {"Accept": "application/json"}
        token = self.creds.access
        if token:
            out["Authorization"] = f"Bearer {token}"
        ws = self.creds.workspace
        if ws:
            out[WORKSPACE_HEADER] = ws
        return out

    def _send(
        self,
        method: str,
        path: str,
        body: Optional[Any],
        query: Optional[Dict[str, Any]],
    ) -> Tuple[int, Any]:
        url = self.creds.api_base.rstrip("/") + path
        clean_query = None
        if query:
            clean_query = {k: v for k, v in query.items() if v not in (None, "")}
            if not clean_query:
                clean_query = None
        try:
            response = self._http.request(
                method,
                url,
                headers=self._headers(),
                json=body,
                params=clean_query,
            )
        except httpx.HTTPError as exc:
            raise CliError(f"network error talking to {url}: {exc}") from exc
        return response.status_code, _decode(response)

    def _refresh_token(self) -> None:
        status, payload = self._send(
            "POST",
            "/api/v1/auth/token/refresh/",
            {"refresh": self.creds.refresh},
            None,
        )
        if status != 200:
            self._store.clear()
            raise AuthError(
                "refresh token rejected — run `briar login` again"
            )
        token = payload.get("access", "") if type(payload) is dict else ""
        if not token:
            raise AuthError("refresh response missing `access` field")
        self.creds.access = token
        new_refresh = (
            payload.get("refresh", "") if type(payload) is dict else ""
        )
        if new_refresh:
            self.creds.refresh = new_refresh
        self._store.save()
