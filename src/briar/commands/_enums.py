"""Closed enumerations for the CLI command surface.

Enums for closed domain sets, registries for open plug-in spaces. Exit codes are intrinsically a finite set
fixed by the CLI contract — they belong in an enum.
"""
from __future__ import annotations

from enum import Enum, IntEnum


class ExitCode(IntEnum):
    """CLI process exit codes returned by Command.run.

    - 0  success
    - 1  general error — any runtime failure the operator can't fix
         via a flag (network, missing creds, agent crashed, …)
    - 2  usage error (argparse-compatible)
    """

    OK = 0
    GENERAL_ERROR = 1
    USAGE_ERROR = 2


class OutputFormat(str, Enum):
    """`--format` choices on Typer; a test keeps them equal to `briar.formatting.FORMATTERS`."""

    TABLE = "table"
    JSON = "json"
    YAML = "yaml"
    CSV = "csv"
    QUIET = "quiet"


class KnowledgeStoreChoice(str, Enum):
    """`--store` choices on Typer; a test keeps them equal to `briar.storage.KNOWLEDGE_STORE_NAMES`."""

    FILE = "file"
    POSTGRES = "postgres"


class JournalStoreChoice(str, Enum):
    """`journal --store` choices; a test keeps them equal to `briar.journal.JOURNAL_STORE_NAMES`."""

    FILE = "file"


class CredentialStoreChoice(str, Enum):
    """`--cred-store` choices; a test keeps them equal to `CredentialStoreRegistry.kinds()`."""

    ENVFILE = "envfile"


class BootstrapKindChoice(str, Enum):
    """`secrets bootstrap --kind` choices; a test keeps them equal to `CredentialBootstrapRegistry.kinds()`."""

    ENVFILE = "envfile"


class ProviderChoice(str, Enum):
    """`--provider` choices; a test keeps them equal to `RepositoryProviderRegistry.kinds()`."""

    GITHUB = "github"
    BITBUCKET = "bitbucket"


class TrackerChoice(str, Enum):
    """`--tracker` choices; a test keeps them equal to `TrackerRegistry.kinds()`."""

    JIRA = "jira"
    GITHUB_ISSUES = "github-issues"
    BITBUCKET_ISSUES = "bitbucket-issues"
    LINEAR = "linear"


class MeetingChoice(str, Enum):
    """`--meeting` choices; a test keeps them equal to `briar.extract._meetings.meeting_kinds()`."""

    FIREFLIES = "fireflies"



class LlmChoice(str, Enum):
    """`--llm` choices; a test keeps them equal to `LLMRegistry.kinds()`."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    BEDROCK = "bedrock"
