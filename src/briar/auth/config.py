"""The `[auth]` block: the login choices of each source for one company.

Decisions G2 to G6, G26 (`docs/decisions/2026-11-10-briar-2-logins-spec.md`)
and H1, H2, H5, H6, H8, H10 (`docs/decisions/2026-11-10-briar-2-logins-pr1.md`).
The same models check `[auth]` of `.briar.toml` and the
`companies.<company>.auth` block of a runbook. Secrets never live here;
they are in `secrets.env` (L3)."""

from __future__ import annotations

import enum
from typing import Any, ClassVar, Mapping, Tuple
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from briar.errors import CliError


class SourceAuth(BaseModel):
    """One source's choices. Unknown keys are errors (H6)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    def method_name(self) -> str:
        """The login method of the source ("" for one with no `method`, like AWS)."""
        method = getattr(self, "method", None)
        return str(method.value) if isinstance(method, enum.Enum) else ""


class GithubMethod(str, enum.Enum):
    # H8: the device flow arrives with spec 005 PR 4; until then a PAT is the only method.
    TOKEN = "token"


class GitlabMethod(str, enum.Enum):
    TOKEN = "token"


class JiraMethod(str, enum.Enum):
    TOKEN = "token"
    COOKIE = "cookie"


class BitbucketMethod(str, enum.Enum):
    TOKEN = "token"
    COOKIE = "cookie"
    ACCESS_TOKEN = "access-token"


class ApiKeyMethod(str, enum.Enum):
    # Linear: like H8, OAuth (the G5 default) arrives with spec 005 PR 4.
    API_KEY = "api-key"


class GithubAuth(SourceAuth):
    method: GithubMethod = GithubMethod.TOKEN
    host: str = "github.com"
    client_id: str = ""


class JiraAuth(SourceAuth):
    method: JiraMethod = JiraMethod.TOKEN
    site: str = ""
    user_agent: str = ""

    SCHEME: ClassVar[str] = "https"

    @field_validator("site")
    @classmethod
    def _origin_only(cls, site: str) -> str:
        """H10: `acme.atlassian.net`, `https://acme.atlassian.net/` and a pasted
        `https://acme.atlassian.net/jira/...` are the same site, `https://acme.atlassian.net`.
        A scheme that is written (a local `http://` Jira) is kept."""
        raw = site.strip()
        if not raw:
            return ""
        parts = urlsplit(raw if "://" in raw else f"{cls.SCHEME}://{raw}")
        return f"{parts.scheme}://{parts.netloc}"


class GitlabAuth(SourceAuth):
    """G18: gitlab.com, or the origin of a self-hosted GitLab."""

    method: GitlabMethod = GitlabMethod.TOKEN
    url: str = "https://gitlab.com"

    HOSTED: ClassVar[str] = "https://gitlab.com"

    @field_validator("url")
    @classmethod
    def _origin_only(cls, url: str) -> str:
        """Like the Jira site: `gitlab.acme.test` and a pasted project URL give the origin.
        ponytail: a GitLab served under a sub path is not kept; add the path when one shows up."""
        raw = url.strip()
        if not raw:
            return cls.HOSTED
        parts = urlsplit(raw if "://" in raw else f"{JiraAuth.SCHEME}://{raw}")
        return f"{parts.scheme}://{parts.netloc}"


class BitbucketAuth(SourceAuth):
    method: BitbucketMethod = BitbucketMethod.TOKEN
    workspace: str = ""


class LinearAuth(SourceAuth):
    method: ApiKeyMethod = ApiKeyMethod.API_KEY
    client_id: str = ""


class FirefliesAuth(SourceAuth):
    method: ApiKeyMethod = ApiKeyMethod.API_KEY


class AwsAuth(SourceAuth):
    profile: str = ""


class GcpAuth(SourceAuth):
    config_dir: str = ""
    project: str = ""


class AzureAuth(SourceAuth):
    tenant: str = ""
    subscription: str = ""


class AuthBlock(SourceAuth):
    """Every source a company can log into. A source left out takes its defaults."""

    github: GithubAuth = Field(default_factory=GithubAuth)
    jira: JiraAuth = Field(default_factory=JiraAuth)
    bitbucket: BitbucketAuth = Field(default_factory=BitbucketAuth)
    gitlab: GitlabAuth = Field(default_factory=GitlabAuth)
    linear: LinearAuth = Field(default_factory=LinearAuth)
    fireflies: FirefliesAuth = Field(default_factory=FirefliesAuth)
    aws: AwsAuth = Field(default_factory=AwsAuth)
    gcp: GcpAuth = Field(default_factory=GcpAuth)
    azure: AzureAuth = Field(default_factory=AzureAuth)

    def source(self, name: str) -> SourceAuth:
        """The choices of one source, by its name (`jira`, `github`, ...)."""
        choices: SourceAuth = getattr(self, name)
        return choices


class AuthTable:
    """Reads and checks the `[auth]` table of a project config (H6: the whole block,
    every time a command reads it)."""

    KEY = "auth"

    def __init__(self, project_config: Mapping[str, Any], origin: str) -> None:
        self._project_config = project_config
        self._origin = origin

    def block(self) -> AuthBlock:
        try:
            return AuthBlock.model_validate(self._project_config.get(self.KEY, {}))
        except ValidationError as error:
            raise CliError(AuthError(error, self._origin).message()) from None


class AuthError:
    """The first problem of a failed `[auth]` check, as one line naming the key."""

    def __init__(self, error: ValidationError, origin: str) -> None:
        self._error = error
        self._origin = origin

    def message(self) -> str:
        first = self._error.errors()[0]
        location = tuple(str(part) for part in first["loc"])
        key = ".".join((AuthTable.KEY, *location))
        detail = f"unknown key; allowed: {self._allowed(location)}" if first["type"] == "extra_forbidden" else first["msg"]
        return f"`{key}` in {self._origin}: {detail}"

    def _allowed(self, location: Tuple[str, ...]) -> str:
        if len(location) == 1:
            return ", ".join(AuthBlock.model_fields)
        return ", ".join(type(AuthBlock().source(location[0])).model_fields)
