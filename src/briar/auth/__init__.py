"""The logins briar uses to read the sources (spec 005).

- ``briar.auth.config``: the `[auth]` block of `.briar.toml` and the runbook
  `auth:` block, one table of choices per source.
- ``briar.auth.logins``: one login per source per company (`CompanyLogins`):
  the secret names, how to get them, and the 1.x names guard.
- ``briar.auth.check``: the login check before a run.
- ``briar.credentials``: the startup bootstrap that loads `secrets.env`, where a
  person writes the secrets by hand (spec 003 part 4, X4)."""
