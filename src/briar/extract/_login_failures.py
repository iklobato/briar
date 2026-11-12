"""How each vendor library says "refused" and "unreachable", for the login check
(spec 005 PR 3). An adapter runs its "who am I" or its read through the one that
matches its client; the check then sees only `LoginRejected` and
`SourceUnreachable` (`briar.auth.check`)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Tuple, Type

from briar.auth.check import LoginRejected, SourceUnreachable
from briar.errors import AuthError


class LibraryFailures(ABC):
    """Runs one call of an adapter and turns the errors of its library into check errors."""

    # The first line of a library message, cut here: some answers carry a whole JSON body.
    DETAIL_LIMIT = 200
    # What an optional SDK that is not installed catches: its call failed before any
    # request, with a `CliError` that says what to install, which then goes through.
    NOTHING: Tuple[Type[BaseException], ...] = ()

    def account(self, action: Callable[[], str]) -> str:
        try:
            return action()
        except self.caught() as error:
            raise self.translated(error) from error

    def read(self, action: Callable[[], object]) -> None:
        try:
            action()
        except self.caught() as error:
            raise self.translated(error) from error

    def detail(self, error: BaseException) -> str:
        text = str(error).strip().splitlines()
        return (text[0] if text else type(error).__name__)[: self.DETAIL_LIMIT]

    def with_status(self, status: int, error: BaseException) -> str:
        """`HTTP <status>`, then the first line of the message when it has one."""
        text = str(error).strip().splitlines()
        return f"HTTP {status}: {text[0][: self.DETAIL_LIMIT]}" if text else f"HTTP {status}"

    @abstractmethod
    def caught(self) -> Tuple[Type[BaseException], ...]:
        """The library errors this call can raise."""

    @abstractmethod
    def translated(self, error: BaseException) -> AuthError:
        """`SourceUnreachable` for a network error, else `LoginRejected`."""


class RequestsFailures(LibraryFailures):
    """atlassian-python-api (Jira, Bitbucket): `requests` errors."""

    def caught(self) -> Tuple[Type[BaseException], ...]:
        import requests

        return (requests.RequestException,)

    def translated(self, error: BaseException) -> AuthError:
        import requests

        if isinstance(error, (requests.ConnectionError, requests.Timeout)):
            return SourceUnreachable(self.detail(error))
        response = getattr(error, "response", None)
        if response is None:
            return LoginRejected(self.detail(error))
        return LoginRejected(self.with_status(response.status_code, error))


class GithubFailures(LibraryFailures):
    """PyGithub: `GithubException` carries the HTTP status; the network errors are `requests` ones."""

    def caught(self) -> Tuple[Type[BaseException], ...]:
        import requests
        from github import GithubException

        return (GithubException, requests.RequestException)

    def translated(self, error: BaseException) -> AuthError:
        from github import GithubException

        if isinstance(error, GithubException):
            return LoginRejected(self.with_status(error.status, error))
        return SourceUnreachable(self.detail(error))


class UrllibFailures(LibraryFailures):
    """The urllib GraphQL calls (Linear, Fireflies). `RuntimeError` is how `_gql` reports
    the errors of a 200 answer."""

    def caught(self) -> Tuple[Type[BaseException], ...]:
        import urllib.error

        return (urllib.error.URLError, TimeoutError, RuntimeError)

    def translated(self, error: BaseException) -> AuthError:
        import urllib.error

        if isinstance(error, urllib.error.HTTPError):
            error.close()
            return LoginRejected(self.with_status(error.code, error))
        if isinstance(error, RuntimeError):
            return LoginRejected(self.detail(error))
        return SourceUnreachable(self.detail(error))


class BotoFailures(LibraryFailures):
    """boto3: `ClientError` (403 on a bad key), the credential errors (no profile, an
    expired SSO login), and the connection errors."""

    def caught(self) -> Tuple[Type[BaseException], ...]:
        from botocore.exceptions import BotoCoreError, ClientError

        return (ClientError, BotoCoreError)

    def translated(self, error: BaseException) -> AuthError:
        from botocore.exceptions import ConnectionError as BotoConnectionError
        from botocore.exceptions import HTTPClientError

        if isinstance(error, (BotoConnectionError, HTTPClientError)):
            return SourceUnreachable(self.detail(error))
        return LoginRejected(self.detail(error))


class GoogleFailures(LibraryFailures):
    """google-auth (the ADC refresh) and googleapiclient (the project read, over httplib2)."""

    def caught(self) -> Tuple[Type[BaseException], ...]:
        try:
            import httplib2
            from google.auth.exceptions import GoogleAuthError
            from googleapiclient.errors import HttpError
        except ImportError:
            return self.NOTHING
        return (GoogleAuthError, HttpError, httplib2.HttpLib2Error, OSError)

    def translated(self, error: BaseException) -> AuthError:
        import httplib2
        from google.auth.exceptions import TransportError

        if isinstance(error, (TransportError, httplib2.HttpLib2Error, OSError)):
            return SourceUnreachable(self.detail(error))
        return LoginRejected(self.detail(error))


class AzureFailures(LibraryFailures):
    """azure-identity and azure-mgmt: every error is an `AzureError`; the two request errors
    are the network ones."""

    def caught(self) -> Tuple[Type[BaseException], ...]:
        try:
            from azure.core.exceptions import AzureError
        except ImportError:
            return self.NOTHING
        return (AzureError,)

    def translated(self, error: BaseException) -> AuthError:
        from azure.core.exceptions import ServiceRequestError, ServiceResponseError

        if isinstance(error, (ServiceRequestError, ServiceResponseError)):
            return SourceUnreachable(self.detail(error))
        return LoginRejected(self.detail(error))


__all__ = ["AzureFailures", "BotoFailures", "GithubFailures", "GoogleFailures", "LibraryFailures", "RequestsFailures", "UrllibFailures"]
