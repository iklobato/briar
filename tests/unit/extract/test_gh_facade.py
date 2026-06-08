"""Boundary tests for the `GithubApi` PyGithub facade (`_gh.py`).

The facade tunnels every call through PyGithub's low-level
`Requester.requestJsonAndCheck(verb, path)`, which returns a
``(headers, payload)`` tuple. We mock at exactly that seam: a fake
Github object exposing the name-mangled `_Github__requester` attribute,
returned from a patched `GithubApi.client`. No network, no real
PyGithub Github construction.

Doc URLs modelled:
- GitHub REST pagination via Link header:
  https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api
  (``Link: <https://api.github.com/...&page=2>; rel="next"``)
- Rate-limit headers:
  https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
  (``X-RateLimit-Remaining`` — lowercased by requests)
- Error envelope / auth failure:
  https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api
  (``GithubException`` carries ``{"message": ..., "documentation_url": ...}``)
"""

from __future__ import annotations

import unittest
from unittest import mock

import pytest
from github import GithubException

from briar.errors import CliError
from briar.extract._gh import GithubApi


class _FakeRequester:
    """Stand-in for PyGithub's private `Requester`. Records the
    (verb, path) of each call and replays a queue of
    ``(headers, payload)`` results, mirroring `requestJsonAndCheck`."""

    def __init__(self, results):
        # results: list of (headers, payload) OR an Exception to raise.
        self._results = list(results)
        self.calls = []

    def requestJsonAndCheck(self, verb, path):
        self.calls.append((verb, path))
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeGithub:
    def __init__(self, requester):
        # PyGithub name-mangles `self.__requester` to `_Github__requester`.
        self._Github__requester = requester

    def get_rate_limit(self):  # pragma: no cover - only hit in fallback path
        raise AssertionError("get_rate_limit should not be called when header present")


def _client_returning(requester):
    fake = _FakeGithub(requester)
    return mock.patch.object(GithubApi, "client", return_value=fake)


# ---------------------------------------------------------------------------
# client() — PyGithub construction
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ClientConstructionTests(unittest.TestCase):
    def test_builds_github_with_token_and_tuning(self) -> None:
        # We patch the constructors PyGithub exposes so no real client is
        # built; assert the boundary config the facade pins (per_page/retry/timeout).
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_real"}):
            with mock.patch("github.Github") as github_cls, mock.patch("github.Auth") as auth_mod:
                auth_mod.Token.return_value = "auth-obj"
                GithubApi.client()
        auth_mod.Token.assert_called_once_with("ghp_real")
        _, kwargs = github_cls.call_args
        self.assertEqual(kwargs["auth"], "auth-obj")
        self.assertEqual(kwargs["per_page"], 100)
        self.assertEqual(kwargs["retry"], 3)
        self.assertEqual(kwargs["timeout"], 30)

    def test_client_raises_clierror_without_token(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(CliError):
                GithubApi.client()


# ---------------------------------------------------------------------------
# auth_token / _require_token
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class AuthTokenTests(unittest.TestCase):
    def test_auth_token_strips_whitespace(self) -> None:
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "  ghp_abc  "}):
            self.assertEqual(GithubApi.auth_token(), "ghp_abc")

    def test_auth_token_empty_when_unset(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(GithubApi.auth_token(), "")

    def test_require_token_prefers_explicit_arg(self) -> None:
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "from_env"}):
            self.assertEqual(GithubApi._require_token("explicit"), "explicit")

    def test_require_token_falls_back_to_env(self) -> None:
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "from_env"}):
            self.assertEqual(GithubApi._require_token(""), "from_env")

    def test_require_token_raises_clierror_when_missing(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(CliError) as ctx:
                GithubApi._require_token("")
        self.assertIn("GitHub credentials missing", str(ctx.exception))


# ---------------------------------------------------------------------------
# get_json
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class GetJsonTests(unittest.TestCase):
    def test_returns_payload_and_passes_verb_and_path(self) -> None:
        payload = {"id": 1, "title": "hi"}  # https://docs.github.com/en/rest/issues
        req = _FakeRequester([({"x-ratelimit-remaining": "4999"}, payload)])
        with _client_returning(req):
            out = GithubApi.get_json("/repos/acme/app/issues/1")
        self.assertEqual(out, payload)
        self.assertEqual(req.calls, [("GET", "/repos/acme/app/issues/1")])

    def test_propagates_github_exception_on_404(self) -> None:
        # 404 envelope per https://docs.github.com/en/rest — get_json does
        # NOT swallow; the @swallow_errors-wrapped caller handles it.
        err = GithubException(404, {"message": "Not Found", "documentation_url": "https://docs.github.com/rest"}, {})
        req = _FakeRequester([err])
        with _client_returning(req):
            with self.assertRaises(GithubException) as ctx:
                GithubApi.get_json("/repos/acme/app/issues/999")
        self.assertEqual(ctx.exception.status, 404)

    def test_propagates_auth_exception_on_401(self) -> None:
        err = GithubException(401, {"message": "Bad credentials"}, {})
        req = _FakeRequester([err])
        with _client_returning(req):
            with self.assertRaises(GithubException) as ctx:
                GithubApi.get_json("/repos/acme/app/issues/1")
        self.assertEqual(ctx.exception.status, 401)


# ---------------------------------------------------------------------------
# get_paginated — Link-header walking
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class GetPaginatedTests(unittest.TestCase):
    def test_single_page_no_next_link(self) -> None:
        rows = [{"number": 1}, {"number": 2}]
        req = _FakeRequester([({"x-ratelimit-remaining": "4998"}, rows)])
        with _client_returning(req):
            out = GithubApi.get_paginated("/repos/acme/app/issues")
        self.assertEqual(out, rows)
        # per_page appended; first path gets the ? separator.
        self.assertEqual(req.calls, [("GET", "/repos/acme/app/issues?per_page=100")])

    def test_follows_next_link_until_exhausted(self) -> None:
        # https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api
        page1_headers = {
            "link": '<https://api.github.com/repos/acme/app/issues?per_page=100&page=2>; rel="next", '
            '<https://api.github.com/repos/acme/app/issues?per_page=100&page=2>; rel="last"',
            "x-ratelimit-remaining": "4997",
        }
        page2_headers = {"x-ratelimit-remaining": "4996"}  # no Link → last page
        req = _FakeRequester(
            [
                (page1_headers, [{"number": 1}, {"number": 2}]),
                (page2_headers, [{"number": 3}]),
            ]
        )
        with _client_returning(req):
            out = GithubApi.get_paginated("/repos/acme/app/issues")
        self.assertEqual([r["number"] for r in out], [1, 2, 3])
        # Second call uses the relative path stripped of the api.github.com base.
        self.assertEqual(req.calls[0], ("GET", "/repos/acme/app/issues?per_page=100"))
        self.assertEqual(req.calls[1], ("GET", "/repos/acme/app/issues?per_page=100&page=2"))

    def test_empty_page_yields_empty_list(self) -> None:
        req = _FakeRequester([({"x-ratelimit-remaining": "5000"}, [])])
        with _client_returning(req):
            out = GithubApi.get_paginated("/repos/acme/app/issues")
        self.assertEqual(out, [])

    def test_non_list_payload_is_skipped_not_appended(self) -> None:
        # If GitHub returns a dict (e.g. an error-shaped body slipped through),
        # `isinstance(page, list)` guards against polluting the result.
        req = _FakeRequester([({"x-ratelimit-remaining": "1"}, {"message": "weird"})])
        with _client_returning(req):
            out = GithubApi.get_paginated("/repos/acme/app/issues")
        self.assertEqual(out, [])

    def test_appends_per_page_with_ampersand_when_query_present(self) -> None:
        req = _FakeRequester([({}, [])])
        with _client_returning(req):
            GithubApi.get_paginated("/repos/acme/app/issues?state=open", per_page=50)
        self.assertEqual(req.calls[0], ("GET", "/repos/acme/app/issues?state=open&per_page=50"))

    def test_respects_max_pages_and_warns_on_truncation(self) -> None:
        # Every page advertises a next link; max_pages=2 must stop after 2.
        next_link = {
            "link": '<https://api.github.com/repos/acme/app/issues?page=2>; rel="next"',
        }
        req = _FakeRequester(
            [
                (next_link, [{"number": 1}]),
                (next_link, [{"number": 2}]),
                (next_link, [{"number": 3}]),  # would be page 3 — must NOT be fetched
            ]
        )
        with _client_returning(req):
            out = GithubApi.get_paginated("/repos/acme/app/issues", max_pages=2)
        self.assertEqual([r["number"] for r in out], [1, 2])
        self.assertEqual(len(req.calls), 2)

    def test_propagates_rate_limit_exception(self) -> None:
        # 403 + X-RateLimit-Remaining: 0 per the rate-limit docs. get_paginated
        # does not swallow — the typed exception surfaces.
        err = GithubException(403, {"message": "API rate limit exceeded"}, {"x-ratelimit-remaining": "0"})
        req = _FakeRequester([err])
        with _client_returning(req):
            with self.assertRaises(GithubException) as ctx:
                GithubApi.get_paginated("/repos/acme/app/issues")
        self.assertEqual(ctx.exception.status, 403)


# ---------------------------------------------------------------------------
# _next_link parsing
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class NextLinkTests(unittest.TestCase):
    def test_empty_header_returns_empty(self) -> None:
        self.assertEqual(GithubApi._next_link(""), "")

    def test_no_next_rel_returns_empty(self) -> None:
        header = '<https://api.github.com/repos/acme/app/issues?page=5>; rel="last"'
        self.assertEqual(GithubApi._next_link(header), "")

    def test_strips_api_base_to_relative_path(self) -> None:
        header = '<https://api.github.com/repos/acme/app/issues?page=2>; rel="next"'
        self.assertEqual(GithubApi._next_link(header), "/repos/acme/app/issues?page=2")

    def test_returns_full_url_when_not_api_github_base(self) -> None:
        # GitHub Enterprise host — base doesn't match, so the full URL is kept.
        header = '<https://ghe.internal/api/v3/repos/x/y?page=2>; rel="next"'
        self.assertEqual(GithubApi._next_link(header), "https://ghe.internal/api/v3/repos/x/y?page=2")

    def test_picks_next_among_multiple_rels(self) -> None:
        header = (
            '<https://api.github.com/r?page=1>; rel="prev", ' '<https://api.github.com/r?page=3>; rel="next", ' '<https://api.github.com/r?page=9>; rel="last"'
        )
        self.assertEqual(GithubApi._next_link(header), "/r?page=3")


# ---------------------------------------------------------------------------
# _extract_rate_remaining
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ExtractRateRemainingTests(unittest.TestCase):
    def test_reads_lowercased_header(self) -> None:
        self.assertEqual(GithubApi._extract_rate_remaining({"x-ratelimit-remaining": "42"}, None), "42")

    def test_reads_canonical_cased_header(self) -> None:
        self.assertEqual(GithubApi._extract_rate_remaining({"X-RateLimit-Remaining": "7"}, None), "7")

    def test_falls_back_to_live_rate_limit_api(self) -> None:
        gh = mock.MagicMock()
        gh.get_rate_limit.return_value.core.remaining = 1234
        self.assertEqual(GithubApi._extract_rate_remaining({}, gh), "1234")

    def test_returns_question_mark_when_fallback_raises(self) -> None:
        gh = mock.MagicMock()
        gh.get_rate_limit.side_effect = RuntimeError("network down")
        self.assertEqual(GithubApi._extract_rate_remaining({}, gh), "?")


if __name__ == "__main__":
    unittest.main()
