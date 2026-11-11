"""`briar auth`: the logins briar uses to read the sources (spec 005).

- ``briar.auth.config``: the `[auth]` block of `.briar.toml` and the runbook
  `auth:` block, one table of choices per source.
- ``briar.auth.logins``: one login per source per company (`CompanyLogins`):
  the secret names, what `briar auth login` asks for, and the 1.x names guard.
- ``_prompt.py``: the terminal (`PromptIO`): prompts, browser, the CLI logins.
- ``briar.credentials``: `EnvFileStore`, the owner-only env file every secret
  goes to (L3), and the startup bootstrap that loads it."""

from __future__ import annotations

from briar.auth._prompt import MockPromptIO, PromptIO, TerminalPromptIO


__all__ = [
    "MockPromptIO",
    "PromptIO",
    "TerminalPromptIO",
]
