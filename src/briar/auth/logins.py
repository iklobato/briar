"""One login per source per company.

A `SourceLogin` knows the secret names of its source (`BRIAR_<COMPANY>_<SOURCE>_<FIELD>`,
G7), what `briar auth login` asks for each method (G5), which config values it
needs (H1), and the 1.x names that stop a run when the 2.0 ones are missing (G8,
G25, H7). Adapters get a `CompanyLogins` and ask it for their source; nothing
here reads `.briar.toml` or a runbook: the callers pass the checked block.
Decisions: `docs/decisions/2026-11-10-briar-2-logins-spec.md`,
`docs/decisions/2026-11-10-briar-2-logins-pr1.md`."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Dict, List, Mapping, Tuple, Type

from briar.auth._prompt import PromptIO
from briar.auth.config import AuthBlock, BitbucketAuth, GithubAuth, JiraAuth, SourceAuth
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


@dataclass(frozen=True)
class SecretField:
    """One value `briar auth login` asks for and writes to `secrets.env`."""

    key: str
    prompt: str
    hidden: bool = True


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

    def ready(self) -> bool:
        """True when the login can be used. Stops (CliError) on a 1.x name the user
        must move (G8) or a missing config value next to saved secrets (H1)."""
        hint = self.legacy_hint()
        if hint:
            raise CliError(hint)
        if self.missing_secrets():
            return False
        self.check_settings()
        return True

    def check_settings(self) -> None:
        for key in self.REQUIRED:
            if not getattr(self._settings, key):
                raise CliError(f"set `{key}` under {self._scope.where(self.SOURCE)} (briar {self.SOURCE} login for {self._scope.company})")

    def legacy_hint(self) -> str:
        """G8: the 1.x names that are set while the 2.0 login is incomplete, with the fix."""
        if not self.missing_secrets():
            return ""
        found = [legacy for legacy in self.LEGACY if self._environ.get(legacy.name(self._scope), "").strip()]
        if not found:
            return ""
        moves = "; ".join(f"`{legacy.name(self._scope)}` is a 1.x name: use {legacy.place.format(p=self._scope.env_prefix())}" for legacy in found)
        return f"{moves}. Or run `briar auth login {self.SOURCE} --company {self._scope.company}`"

    def login(self, prompt: PromptIO) -> Dict[str, str]:
        """Ask for this method's values and return them by secret name (nothing is written here)."""
        self.check_settings()
        prompt.info(f"==> {self.SOURCE} login for {self._scope.company} (method: {self.method()})")
        prompt.info(f"    {self.HELP[self.method()]}")
        entries = {self.name(field.key): prompt.prompt(f"    {field.prompt}: ", secret=field.hidden).strip() for field in self.FIELDS[self.method()]}
        empty = [name for name, value in entries.items() if not value]
        if empty:
            raise CliError(f"{self.SOURCE} login: nothing entered for {', '.join(empty)}")
        return entries


class JiraLogin(SourceLogin):
    SOURCE = "jira"
    FIELDS = {
        "token": (SecretField("EMAIL", "Atlassian account email", hidden=False), SecretField("TOKEN", "API token")),
        "cookie": (SecretField("COOKIE", "the Cookie header of a request to your Jira site"),),
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
        "token": (SecretField("EMAIL", "Atlassian account email", hidden=False), SecretField("TOKEN", "API token (with Bitbucket scopes)")),
        "access-token": (SecretField("ACCESS_TOKEN", "workspace or repository access token"),),
        "cookie": (SecretField("COOKIE", "the Cookie header of a request to bitbucket.org"),),
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
    FIELDS = {"api-key": (SecretField("API_KEY", "personal API key"),)}
    HELP = {"api-key": "Create a personal API key at https://linear.app/settings/api"}
    LEGACY = (LegacyName("LINEAR_{c}_TOKEN", '`{p}_LINEAR_API_KEY` with `method = "api-key"`'),)

    def authorization(self) -> str:
        """Linear takes the personal API key as the bare `Authorization` header (no `Bearer`)."""
        return self.secret("API_KEY")


class FirefliesLogin(SourceLogin):
    SOURCE = "fireflies"
    FIELDS = {"api-key": (SecretField("API_KEY", "API key"),)}
    HELP = {"api-key": "Copy the API key from https://app.fireflies.ai/settings (Developer settings)"}
    LEGACY = (LegacyName("FIREFLIES_{c}_API_KEY", "`{p}_FIREFLIES_API_KEY`"),)

    def api_key(self) -> str:
        return self.secret("API_KEY")


class GithubLogin(SourceLogin):
    SOURCE = "github"
    FIELDS = {"token": (SecretField("TOKEN", "fine-grained personal access token"),)}
    HELP = {"token": "Create a fine-grained token at https://github.com/settings/personal-access-tokens/new (read access)"}
    # G25: the global 1.x token; briar no longer reads it.
    LEGACY = (LegacyName("GITHUB_TOKEN", '`{p}_GITHUB_TOKEN` with `method = "token"`'),)

    def __init__(self, scope: LoginScope, settings: GithubAuth, environ: Mapping[str, str]) -> None:
        super().__init__(scope, settings, environ)
        self._github = settings

    def token(self) -> str:
        return self.secret("TOKEN")

    def host(self) -> str:
        return self._github.host


class CompanyLogins:
    """The logins of one company: what each adapter asks for its source."""

    SOURCES: ClassVar[Mapping[str, Type[SourceLogin]]] = {
        "github": GithubLogin,
        "jira": JiraLogin,
        "bitbucket": BitbucketLogin,
        "linear": LinearLogin,
        "fireflies": FirefliesLogin,
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


__all__ = [
    "BitbucketLogin",
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
