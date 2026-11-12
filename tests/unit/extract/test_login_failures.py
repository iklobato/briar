"""How each vendor library says "refused" and "unreachable" in the login check
(spec 005 PR 3, J3, J4). The errors are the real classes of each library."""

from __future__ import annotations

import io
import sys
import urllib.error
from dataclasses import dataclass
from functools import partial
from typing import List

import pytest
import requests
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from github import GithubException

from briar.auth.check import LoginRejected, SourceUnreachable
from briar.errors import CliError
from briar.extract._login_failures import AzureFailures, BotoFailures, GithubFailures, GoogleFailures, LibraryFailures, RequestsFailures, UrllibFailures

UNAUTHORIZED = 401
NOT_FOUND = 404
LONG = 500


@dataclass(frozen=True)
class Raising:
    """A call that raises `error`."""

    error: BaseException

    def account(self) -> str:
        raise self.error


@dataclass(frozen=True)
class Case:
    failures: LibraryFailures
    error: BaseException
    kind: type
    message: str


class Errors:
    """Real errors of each library, built the way the library builds them."""

    def http_401(self) -> urllib.error.HTTPError:
        return urllib.error.HTTPError("https://api.linear.app/graphql", UNAUTHORIZED, "Unauthorized", {}, io.BytesIO(b""))

    def requests_401(self) -> requests.HTTPError:
        response = requests.Response()
        response.status_code = UNAUTHORIZED
        return requests.HTTPError("401 Client Error: Unauthorized for url: https://acme.atlassian.net/rest/api/2/myself", response=response)

    def requests_with_no_message(self) -> requests.HTTPError:
        """How atlassian raises a 404 with no error body."""
        response = requests.Response()
        response.status_code = NOT_FOUND
        return requests.HTTPError(response=response)

    def boto_bad_key(self) -> ClientError:
        answer = {"Error": {"Code": "InvalidClientTokenId", "Message": "The security token included in the request is invalid."}}
        return ClientError(answer, "GetCallerIdentity")


CASES = (
    Case(
        RequestsFailures(),
        Errors().requests_401(),
        LoginRejected,
        "HTTP 401: 401 Client Error: Unauthorized for url: https://acme.atlassian.net/rest/api/2/myself",
    ),
    Case(RequestsFailures(), Errors().requests_with_no_message(), LoginRejected, "HTTP 404"),
    Case(RequestsFailures(), requests.TooManyRedirects("Exceeded 30 redirects."), LoginRejected, "Exceeded 30 redirects."),
    Case(RequestsFailures(), requests.ConnectionError("Failed to resolve acme.atlassian.net"), SourceUnreachable, "Failed to resolve acme.atlassian.net"),
    Case(RequestsFailures(), requests.Timeout("read timed out"), SourceUnreachable, "read timed out"),
    Case(GithubFailures(), GithubException(UNAUTHORIZED, {"message": "Bad credentials"}), LoginRejected, 'HTTP 401: 401 {"message": "Bad credentials"}'),
    Case(GithubFailures(), requests.ConnectionError("Max retries exceeded"), SourceUnreachable, "Max retries exceeded"),
    Case(UrllibFailures(), Errors().http_401(), LoginRejected, "HTTP 401: HTTP Error 401: Unauthorized"),
    Case(UrllibFailures(), urllib.error.URLError("nodename nor servname provided"), SourceUnreachable, "<urlopen error nodename nor servname provided>"),
    Case(UrllibFailures(), TimeoutError("timed out"), SourceUnreachable, "timed out"),
    Case(
        UrllibFailures(),
        RuntimeError("linear graphql errors: [{'message': 'Authentication required'}]"),
        LoginRejected,
        "linear graphql errors: [{'message': 'Authentication required'}]",
    ),
    Case(
        BotoFailures(),
        Errors().boto_bad_key(),
        LoginRejected,
        "An error occurred (InvalidClientTokenId) when calling the GetCallerIdentity operation: The security token included in the request is invalid.",
    ),
    Case(BotoFailures(), NoCredentialsError(), LoginRejected, "Unable to locate credentials"),
    Case(
        BotoFailures(),
        EndpointConnectionError(endpoint_url="https://sts.amazonaws.com/"),
        SourceUnreachable,
        'Could not connect to the endpoint URL: "https://sts.amazonaws.com/"',
    ),
)


class TestTranslation:
    @pytest.mark.parametrize("case", CASES, ids=[f"{type(case.failures).__name__}-{type(case.error).__name__}" for case in CASES])
    def test_each_library_error_becomes_a_check_error(self, case: Case) -> None:
        with pytest.raises(case.kind) as caught:
            case.failures.account(Raising(case.error).account)

        assert (str(caught.value), caught.value.__cause__) == (case.message, case.error)

    def test_an_error_the_library_does_not_raise_is_not_hidden(self) -> None:
        with pytest.raises(KeyError):
            RequestsFailures().account(Raising(KeyError("viewer")).account)

    def test_a_read_that_passes_returns_nothing(self) -> None:
        calls: List[str] = []

        assert (RequestsFailures().read(partial(calls.append, "read")), calls) == (None, ["read"])


class TestOptionalSdks:
    """The GCP and Azure SDKs are extras. With one missing, the call fails before any
    request (`_credentials` raises a `CliError` that says what to install): that error
    goes through as it is, never an ImportError of the translator."""

    @pytest.mark.parametrize("failures, module", [(GoogleFailures(), "httplib2"), (AzureFailures(), "azure.core.exceptions")], ids=["gcp", "azure"])
    def test_a_missing_sdk_lets_the_error_of_the_call_through(self, failures: LibraryFailures, module: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, module, None)
        missing = CliError("google-auth not installed: run `pip install 'briar-cli[gcp]'`")

        with pytest.raises(CliError) as caught:
            failures.account(Raising(missing).account)

        assert caught.value is missing


class TestDetail:
    def test_keeps_the_first_line_of_a_long_answer(self) -> None:
        error = RuntimeError("linear graphql errors: bad\n" + "x" * LONG)

        assert RequestsFailures().detail(error) == "linear graphql errors: bad"

    def test_cuts_a_long_first_line(self) -> None:
        assert len(RequestsFailures().detail(RuntimeError("y" * LONG))) == LibraryFailures.DETAIL_LIMIT

    def test_an_empty_message_gives_the_error_name(self) -> None:
        assert RequestsFailures().detail(KeyError()) == "KeyError"
