"""One login per source per company.

A `SourceLogin` knows the secret names of its source (`BRIAR_<COMPANY>_<SOURCE>_<FIELD>`,
G7), the values each method needs (G5) and how to get them, which config values it
needs (H1), and the 1.x names that stop a run when the 2.0 ones are missing (G8,
G25, H7). Adapters get a `CompanyLogins` and ask it for their source; nothing
here reads `.briar.toml` or a runbook: the callers pass the checked block.
Decisions: `docs/decisions/2026-11-10-briar-2-logins-spec.md`,
`docs/decisions/2026-11-10-briar-2-logins-pr1.md`, `docs/decisions/2026-11-11-briar-2-logins-pr2.md`,
`docs/decisions/2026-11-11-briar-2-logins-pr3.md`. Since spec 003 part 4 (X4) the
secrets are written by hand, in the environment or in `secrets.env`."""

from __future__ import annotations

import os
import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Mapping, Optional, Tuple, Type

from briar.auth.config import AuthBlock, AwsAuth, AzureAuth, BitbucketAuth, GcpAuth, GithubAuth, JiraAuth, SourceAuth
from briar.credentials._paths import secrets_path
from briar.errors import CliError


@dataclass(frozen=True)
class LoginScope:
    """The company, and where its `[auth]` choices live (for the messages)."""

    company: str
    origin: str = ".briar.toml"
    table: str = "auth"

    def env_prefix(self) -> str:
        if not self.company:
            raise CliError("no company: pass --company or set `company` in .briar.toml")
        return f"BRIAR_{self.legacy_token()}"

    def legacy_token(self) -> str:
        """The company as 1.x wrote it in env var names: upper case, `-` as `_`."""
        return self.company.upper().replace("-", "_")

    def where(self, source: str) -> str:
        return f"`{self.table}.{source}` in {self.origin}"

    def secrets_place(self) -> str:
        """Where a person writes a secret (S7): the environment, or the `secrets.env` the
        startup bootstrap reads."""
        return f"in the environment or in {secrets_path()}"


@dataclass(frozen=True)
class SecretField:
    """One value of a login method, as the last part of its `BRIAR_*` name."""

    key: str


@dataclass(frozen=True)
class LegacyName:
    """A 1.x env var name (`{c}` is the company) and where its value goes in 2.0."""

    template: str
    place: str

    def name(self, scope: LoginScope) -> str:
        return self.template.format(c=scope.legacy_token())


class SourceLogin:
    """Base of the per-source logins. A subclass fills the class attributes."""

    SOURCE: ClassVar[str] = ""
    FIELDS: ClassVar[Mapping[str, Tuple[SecretField, ...]]] = {}
    HELP: ClassVar[Mapping[str, str]] = {}
    REQUIRED: ClassVar[Tuple[str, ...]] = ()
    LEGACY: ClassVar[Tuple[LegacyName, ...]] = ()

    def __init__(self, scope: LoginScope, settings: SourceAuth, environ: Mapping[str, str]) -> None:
        self._scope = scope
        self._settings = settings
        self._environ = environ

    def method(self) -> str:
        return self._settings.method_name()

    def company(self) -> str:
        return self._scope.company

    def fix(self) -> str:
        """How to give briar this login."""
        return f"set {' and '.join(self.names())} {self._scope.secrets_place()} ({self.HELP[self.method()]})"

    def names(self) -> List[str]:
        return [self.name(field.key) for field in self.FIELDS[self.method()]]

    def name(self, key: str) -> str:
        return f"{self._scope.env_prefix()}_{self.SOURCE.upper()}_{key}"

    def secret(self, key: str) -> str:
        """The saved value, or "" (also for a login of no company, which has no secrets)."""
        if not self._scope.company:
            return ""
        return self._environ.get(self.name(key), "").strip()

    def missing_secrets(self) -> List[str]:
        return [name for name in self.names() if not self._environ.get(name, "").strip()]

    def has_secret(self) -> bool:
        """Any secret of this source is set, for any method, or a 1.x name to move (J10, G8)."""
        names = {self.name(field.key) for fields in self.FIELDS.values() for field in fields}
        return any(self._environ.get(name, "").strip() for name in names) or bool(self.legacy_hint())

    def problem(self) -> str:
        """Why the login cannot be used, with its fix, or "": a 1.x name to move (G8), no
        secret saved, or a missing config value (H1). The check shows it (G13)."""
        hint = self.legacy_hint()
        if hint:
            return hint
        missing = self.missing_secrets()
        if missing:
            return f"no login saved ({', '.join(missing)}); {self.fix()}"
        return self.settings_problem()

    def settings_problem(self) -> str:
        for key in self.REQUIRED:
            if not getattr(self._settings, key):
                return f"set `{key}` under {self._scope.where(self.SOURCE)} (briar {self.SOURCE} login for {self._scope.company})"
        return ""

    def check_settings(self) -> None:
        problem = self.settings_problem()
        if problem:
            raise CliError(problem)

    def incomplete(self) -> bool:
        """The 2.0 login is not all there (the G8 guard looks for 1.x names only then)."""
        return bool(self.missing_secrets())

    def legacy_hint(self) -> str:
        """G8: the 1.x names that are set while the 2.0 login is incomplete, with the fix."""
        if not self.incomplete():
            return ""
        found = [legacy for legacy in self.LEGACY if self._environ.get(legacy.name(self._scope), "").strip()]
        if not found:
            return ""
        moves = "; ".join(f"`{legacy.name(self._scope)}` is a 1.x name: use {legacy.place.format(p=self._scope.env_prefix())}" for legacy in found)
        return moves


class JiraLogin(SourceLogin):
    SOURCE = "jira"
    FIELDS = {
        "token": (SecretField("EMAIL"), SecretField("TOKEN")),
        "cookie": (SecretField("COOKIE"),),
    }
    HELP = {
        "token": "Create an API token at https://id.atlassian.com/manage-profile/security/api-tokens",
        "cookie": "Open your Jira site in the browser, then copy the Cookie request header from DevTools > Network",
    }
    REQUIRED = ("site",)
    LEGACY = (
        LegacyName("JIRA_{c}_EMAIL", "`{p}_JIRA_EMAIL`"),
        LegacyName("JIRA_{c}_TOKEN", "`{p}_JIRA_TOKEN`"),
        LegacyName("JIRA_{c}_URL", "`site` under `[auth.jira]`"),
        LegacyName("JIRA_{c}_AUTH_KIND", "`method` under `[auth.jira]`"),
        LegacyName("JIRA_{c}_SESSION_TOKEN", '`{p}_JIRA_COOKIE` with `method = "cookie"`'),
        LegacyName("JIRA_{c}_TENANT_SESSION_TOKEN", '`{p}_JIRA_COOKIE` with `method = "cookie"`'),
        LegacyName("JIRA_{c}_XSRF_TOKEN", '`{p}_JIRA_COOKIE` with `method = "cookie"`'),
        LegacyName("JIRA_{c}_USER_AGENT", "`user_agent` under `[auth.jira]`"),
    )
    # Sent with the cookie method when `user_agent` is unset (the 1.x default, a desktop browser).
    BROWSER_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"

    def __init__(self, scope: LoginScope, settings: JiraAuth, environ: Mapping[str, str]) -> None:
        super().__init__(scope, settings, environ)
        self._jira = settings

    def site_url(self) -> str:
        self.check_settings()
        return self._jira.site

    def user_agent(self) -> str:
        return self._jira.user_agent or self.BROWSER_USER_AGENT


class BitbucketLogin(SourceLogin):
    SOURCE = "bitbucket"
    FIELDS = {
        "token": (SecretField("EMAIL"), SecretField("TOKEN")),
        "access-token": (SecretField("ACCESS_TOKEN"),),
        "cookie": (SecretField("COOKIE"),),
    }
    HELP = {
        "token": "Create an API token with Bitbucket scopes at https://id.atlassian.com/manage-profile/security/api-tokens",
        "access-token": "Create one in the Bitbucket workspace or repository settings, under Access tokens",
        "cookie": "Open bitbucket.org in the browser, then copy the Cookie request header from DevTools > Network (read only)",
    }
    REQUIRED = ("workspace",)
    LEGACY = (
        LegacyName("BITBUCKET_{c}_USERNAME", "`{p}_BITBUCKET_EMAIL` (app passwords are gone: create an Atlassian API token)"),
        LegacyName("BITBUCKET_{c}_APP_PASSWORD", "`{p}_BITBUCKET_TOKEN` (an Atlassian API token)"),
        LegacyName("BITBUCKET_{c}_WORKSPACE", "`workspace` under `[auth.bitbucket]`"),
    )
    # How `atlassian.bitbucket.cloud.Cloud` takes each method.
    CLIENT_ARGS: ClassVar[Mapping[str, str]] = {"token": "_basic", "access-token": "_bearer", "cookie": "_cookie"}
    # The (user, token) secret keys git and the PR API use; the cookie cannot push (L13).
    PUSH_KEYS: ClassVar[Mapping[str, Tuple[str, str]]] = {"token": ("EMAIL", "TOKEN"), "access-token": ("", "ACCESS_TOKEN")}

    def __init__(self, scope: LoginScope, settings: BitbucketAuth, environ: Mapping[str, str]) -> None:
        super().__init__(scope, settings, environ)
        self._bitbucket = settings

    def workspace(self) -> str:
        return self._bitbucket.workspace

    def has_user(self) -> bool:
        """A workspace or repository access token belongs to no user, so the check reads the
        workspace and the repos instead of `/user` (not checked on a real token yet)."""
        return self.method() != "access-token"

    def client_kwargs(self) -> Dict[str, Any]:
        builder: Callable[[], Dict[str, Any]] = getattr(self, self.CLIENT_ARGS[self.method()])
        return builder()

    def push_token(self) -> str:
        """The token git can push with (none for the read-only cookie)."""
        return self.secret(self._push_keys()[1])

    def push_env_names(self) -> Tuple[str, str]:
        """The env var names of the user ("" for a Bearer token) and the token that the
        PR script reads (spec 003)."""
        user_key, token_key = self._push_keys()
        return (self.name(user_key) if user_key else "", self.name(token_key))

    def _push_keys(self) -> Tuple[str, str]:
        return self.PUSH_KEYS.get(self.method(), ("", ""))

    def _basic(self) -> Dict[str, Any]:
        return {"username": self.secret("EMAIL"), "password": self.secret("TOKEN")}

    def _bearer(self) -> Dict[str, Any]:
        return {"token": self.secret("ACCESS_TOKEN")}

    def _cookie(self) -> Dict[str, Any]:
        import requests

        session = requests.Session()
        session.headers.update({"Cookie": self.secret("COOKIE")})
        return {"session": session}


class LinearLogin(SourceLogin):
    SOURCE = "linear"
    FIELDS = {"api-key": (SecretField("API_KEY"),)}
    HELP = {"api-key": "Create a personal API key at https://linear.app/settings/api"}
    LEGACY = (LegacyName("LINEAR_{c}_TOKEN", '`{p}_LINEAR_API_KEY` with `method = "api-key"`'),)

    def authorization(self) -> str:
        """Linear takes the personal API key as the bare `Authorization` header (no `Bearer`)."""
        return self.secret("API_KEY")


class FirefliesLogin(SourceLogin):
    SOURCE = "fireflies"
    FIELDS = {"api-key": (SecretField("API_KEY"),)}
    HELP = {"api-key": "Copy the API key from https://app.fireflies.ai/settings (Developer settings)"}
    LEGACY = (LegacyName("FIREFLIES_{c}_API_KEY", "`{p}_FIREFLIES_API_KEY`"),)

    def api_key(self) -> str:
        return self.secret("API_KEY")


class GithubLogin(SourceLogin):
    SOURCE = "github"
    FIELDS = {"token": (SecretField("TOKEN"),)}
    HELP = {
        "token": "Create a fine-grained token at https://github.com/settings/personal-access-tokens/new: read access, and read and write on Contents, Pull requests and Issues for `briar serve`"
    }
    # G25: the global 1.x token; briar no longer reads it.
    LEGACY = (LegacyName("GITHUB_TOKEN", '`{p}_GITHUB_TOKEN` with `method = "token"`'),)

    def __init__(self, scope: LoginScope, settings: GithubAuth, environ: Mapping[str, str]) -> None:
        super().__init__(scope, settings, environ)
        self._github = settings

    def token(self) -> str:
        return self.secret("TOKEN")

    def host(self) -> str:
        return self._github.host


class CliLogin(SourceLogin, ABC):
    """A source whose login lives in its own CLI (L3, L14, I1 to I3): a person runs the
    CLI login; runs read what the CLI keeps."""

    CLI: ClassVar[str] = ""
    FIELDS = {"": ()}

    def names(self) -> List[str]:
        return []

    def problem(self) -> str:
        """A run needs no value (I6: then the default of the cloud); only a 1.x name stops it (I10)."""
        return self.legacy_hint()

    def fix(self) -> str:
        return f"run `{self.command_line()}`"

    @abstractmethod
    def command_line(self) -> str:
        """The CLI login command line for this company."""


class AwsLogin(CliLogin):
    SOURCE = "aws"
    CLI = "aws"
    REQUIRED = ("profile",)
    LEGACY = (
        LegacyName("AWS_{c}_ACCESS_KEY_ID", "an AWS profile, named in `profile` under `[auth.aws]`"),
        LegacyName("AWS_{c}_SECRET_ACCESS_KEY", "an AWS profile, named in `profile` under `[auth.aws]`"),
        LegacyName("AWS_{c}_SESSION_TOKEN", "an AWS profile, named in `profile` under `[auth.aws]`"),
        LegacyName("AWS_{c}_REGION", "`region` under `[extract.aws-infra]`"),
    )
    # The AWS config keys of an SSO profile (I5: without them, `aws configure`).
    SSO_KEYS: ClassVar[Tuple[str, ...]] = ("sso_session", "sso_start_url")

    def __init__(self, scope: LoginScope, settings: AwsAuth, environ: Mapping[str, str]) -> None:
        super().__init__(scope, settings, environ)
        self._aws = settings

    def profile(self) -> str:
        return self._aws.profile

    def incomplete(self) -> bool:
        return not self._aws.profile

    def command_line(self) -> str:
        verb = ("sso", "login") if self._uses_sso() else ("configure",)
        return shlex.join((self.CLI, *verb, "--profile", self._aws.profile))

    def _uses_sso(self) -> bool:
        settings = self._profiles().get(self._aws.profile, {})
        return any(key in settings for key in self.SSO_KEYS)

    def _profiles(self) -> Mapping[str, Mapping[str, str]]:
        import botocore.session

        profiles: Mapping[str, Mapping[str, str]] = botocore.session.Session().full_config.get("profiles", {})
        return profiles


class GcpLogin(CliLogin):
    SOURCE = "gcp"
    CLI = "gcloud"
    REQUIRED = ("config_dir",)
    ADC_FILE = "application_default_credentials.json"
    DEFAULT_DIR = "~/.config/gcloud"

    def __init__(self, scope: LoginScope, settings: GcpAuth, environ: Mapping[str, str]) -> None:
        super().__init__(scope, settings, environ)
        self._gcp = settings

    def project(self) -> str:
        return self._gcp.project

    def adc_file(self) -> Optional[Path]:
        """The ADC file of the company config folder (G26); None: the google-auth default chain."""
        return self._folder() / self.ADC_FILE if self._gcp.config_dir else None

    def command_line(self) -> str:
        return f"CLOUDSDK_CONFIG={shlex.quote(str(self._folder()))} {shlex.join((self.CLI, 'auth', 'application-default', 'login'))}"

    def _folder(self) -> Path:
        return Path(self._gcp.config_dir or self.DEFAULT_DIR).expanduser()


class AzureLogin(CliLogin):
    SOURCE = "azure"
    CLI = "az"
    REQUIRED = ("tenant",)

    def __init__(self, scope: LoginScope, settings: AzureAuth, environ: Mapping[str, str]) -> None:
        super().__init__(scope, settings, environ)
        self._azure = settings

    def tenant(self) -> str:
        return self._azure.tenant

    def subscription(self) -> str:
        return self._azure.subscription

    def command_line(self) -> str:
        return shlex.join((self.CLI, "login", "--tenant", self._azure.tenant))


class CompanyLogins:
    """The logins of one company: what each adapter asks for its source."""

    SOURCES: ClassVar[Mapping[str, Type[SourceLogin]]] = {
        "github": GithubLogin,
        "jira": JiraLogin,
        "bitbucket": BitbucketLogin,
        "linear": LinearLogin,
        "fireflies": FirefliesLogin,
        "aws": AwsLogin,
        "gcp": GcpLogin,
        "azure": AzureLogin,
    }

    def __init__(self, scope: LoginScope, block: AuthBlock, environ: Mapping[str, str]) -> None:
        self._scope = scope
        self._block = block
        self._environ = environ

    @classmethod
    def defaults(cls, company: str) -> "CompanyLogins":
        """No `[auth]` block: every source takes its defaults (adapters built in tests, or a
        company with nothing configured). Reads the process environment, where the
        startup bootstrap loaded `secrets.env`."""
        return cls(LoginScope(company), AuthBlock(), os.environ)

    @classmethod
    def from_args(cls, args: Any) -> "CompanyLogins":
        """The logins `briar extract` or the runbook executor put on an extractor namespace;
        the defaults of `args.company` when there are none."""
        found = vars(args).get("logins")
        return found if isinstance(found, CompanyLogins) else cls.defaults(vars(args).get("company") or "")

    @property
    def company(self) -> str:
        return self._scope.company

    def configured(self) -> List[str]:
        """The sources with an `[auth.<source>]` block or a saved secret (J10)."""
        written = self._block.model_fields_set
        return [name for name in self.SOURCES if name in written or self.source(name).has_secret()]

    def source(self, name: str) -> SourceLogin:
        if name not in self.SOURCES:
            raise CliError(f"unknown login source {name!r}; known: {', '.join(self.SOURCES)}")
        return self.SOURCES[name](self._scope, self._block.source(name), self._environ)

    def github(self) -> GithubLogin:
        return GithubLogin(self._scope, self._block.github, self._environ)

    def jira(self) -> JiraLogin:
        return JiraLogin(self._scope, self._block.jira, self._environ)

    def bitbucket(self) -> BitbucketLogin:
        return BitbucketLogin(self._scope, self._block.bitbucket, self._environ)

    def linear(self) -> LinearLogin:
        return LinearLogin(self._scope, self._block.linear, self._environ)

    def fireflies(self) -> FirefliesLogin:
        return FirefliesLogin(self._scope, self._block.fireflies, self._environ)

    def aws(self) -> AwsLogin:
        return AwsLogin(self._scope, self._block.aws, self._environ)

    def gcp(self) -> GcpLogin:
        return GcpLogin(self._scope, self._block.gcp, self._environ)

    def azure(self) -> AzureLogin:
        return AzureLogin(self._scope, self._block.azure, self._environ)


__all__ = [
    "AwsLogin",
    "AzureLogin",
    "BitbucketLogin",
    "CliLogin",
    "GcpLogin",
    "CompanyLogins",
    "FirefliesLogin",
    "GithubLogin",
    "JiraLogin",
    "LegacyName",
    "LinearLogin",
    "LoginScope",
    "SecretField",
    "SourceLogin",
]
