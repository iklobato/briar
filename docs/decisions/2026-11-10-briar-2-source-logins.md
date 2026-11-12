# briar 2.0: how briar logs in to the sources it reads

Grill session of 2026-11-10 (second of the day, after
`2026-11-10-briar-2-pipeline.md`). It covers the logins briar uses to read
the sources (GitHub, Bitbucket, Jira, Linear, Slack, Fireflies, AWS, GCP,
Azure) and to write where the pipeline writes (push, PR, comments). Every
line is a decision the maintainer picked. Facts are in "Evidence".

## Decisions

Format: `ID. Decision: chosen answer. Why. (depends on)`

### Model

- L1. Login model: ready-made libraries first. OAuth that a public CLI can use (no client secret) is the default; where none exists, a pasted cookie string; an API key only where neither fits. (none)
- L2. Accounts: one account per source per company, for every source, GitHub included (1.x has one global `GITHUB_TOKEN`). (none)
- L3. Secrets: what briar holds (pasted tokens, cookies, OAuth refresh tokens) goes to the owner-only env file (`secrets.env`, mode 0600). What `gh`, `aws`, `gcloud` and `az` already hold is read when needed and never copied. (D33)
- L4. Check before running: before `extract` and before any pipeline, briar calls one "who am I" per source with the company's login and checks the permission the run needs. It stops early with the fix. (L2)
- L5. Permissions: one login per source per company, asking for the least it needs: read everywhere, write only where the pipeline writes (GitHub, Bitbucket, Jira comments). The check in L4 says when write is missing. (L2, L4)
- L6. Servers without a browser (`runbook serve`, Docker, CI): log in on the server with a device flow where one exists (`gh`, `aws sso login --use-device-code`); everything else arrives as env vars or a `secrets.env`. (L1)
- L7. Config: the runbook gets a `companies.<company>.auth:` block that says, per source, the method, the account or profile, and an optional OAuth `client_id` override. (L2, D12)
- L8. Expiry: briar renews by itself where it can (OAuth refresh tokens; the CLIs renew their own); where it cannot, it stops before the run with `briar auth login <source> --company <company>`. (L1)

### Cookies and keys

- L9. Cookies: the user pastes the browser's `Cookie:` header string; briar uses it as is and never reads the browser or its keychain. A value that is not a cookie (Slack's `xoxc` token) is a second field. (L1)
- L10. A pasted cookie is allowed for any web source as an alternative to OAuth or a token, for companies that block third-party apps or tokens. (L9)
- L11. LLM keys (OpenRouter, Anthropic) stay plain env vars, outside `briar auth`. (D2)

### Per source

- L12. OAuth apps: briar registers its own apps where it uses OAuth (Linear and Slack, after L14 and L15). The `client_id` is public and lives in the code; a company that requires its own app sets `client_id` in its `auth:` block. (L1, L7)
- L13. Jira and Bitbucket: a scoped Atlassian API token (lifetime 1 to 365 days) is the default for both; a pasted cookie is the fallback. Jira has no OAuth without a client secret; Bitbucket has none either, its app passwords are gone, and a cookie cannot `git push`. (L1, L10)
- L14. AWS, GCP, Azure: reuse the official CLI logins with a profile per company: the AWS profile (`sso-session`), the gcloud configuration or ADC file, the Azure tenant. No OAuth of briar's own. (L1, L3)
- L15. GitHub: only `gh`. briar reads `gh auth token --user <account> --hostname <host>`. No briar OAuth app and no pasted PAT. (L1, L2)
- L16. Slack: OAuth with PKCE through briar's Slack app (user token `xoxp`, refresh lasts 30 days); fallback is the pasted `d` cookie plus the `xoxc` token. (L12, L9)
- L17. Linear: OAuth with PKCE is the default; a personal API key is accepted as an alternative (Linear has no device flow, so this is the way in on a server without a browser). (L12, L6)
- L18. Fireflies: API key (no OAuth exists). (L1)

### Building it

- L19. OAuth library: `authlib` for PKCE and refresh (it uses the `httpx` briar already has); the localhost listener for the redirect uses the standard library. (L1)
- L20. `briar auth login <source>` for a CLI-backed source runs the CLI command for the company's profile in the terminal (`gh auth login`, `aws sso login --profile P`, `gcloud auth application-default login`, `az login --tenant T`) and then runs the L4 check. (L14, L15, L4)
- L21. Plan: a new spec 005, right after spec 002 (CLI on Typer) and before spec 003 (pipeline engine, which needs the L4 check). (pipeline D35)

## What follows from the decisions (confirm with the file)

| Source | Default login | Alternative | On a server |
|---|---|---|---|
| GitHub | `gh` (account per company) | none | `gh auth login` device flow, or `GH_TOKEN` |
| Jira | Atlassian API token | pasted cookie | env var |
| Bitbucket | Atlassian API token (push needs `write:repository:bitbucket`) | pasted cookie (read only) | env var |
| Linear | OAuth PKCE (briar app) | personal API key | API key or env var |
| Slack | OAuth PKCE (briar app), user token | pasted `d` cookie + `xoxc` | env var |
| Fireflies | API key | pasted cookie (L10) | env var |
| AWS | `aws sso login`, profile per company | static keys in an AWS profile | device code |
| GCP | gcloud ADC, configuration per company | pasted cookie does not apply | env var (`GOOGLE_APPLICATION_CREDENTIALS`) |
| Azure | `az login`, tenant per company | none | `az login --use-device-code` |

1.x logins this replaces: `github-pat`, `github-device` (L15), `aws-sso`, `aws-static` (L14), `bitbucket-app-password` (L13), `jira-session` (replaced by the generic pasted cookie, L9). Kept: `jira-token` (becomes the Atlassian API token for Jira and Bitbucket), `linear-api-key` (alternative, L17), `fireflies`. New: Linear OAuth, Slack OAuth, pasted cookie, Slack cookie + `xoxc`. This replaces the pipeline file's D33 note that "the 6 paste-key acquirers stay as they are".

New dependency: `authlib` (pulls `cryptography` and `joserfc`).

## Open risks

- R1. Linear and Slack rotate refresh tokens. Parallel cards (pipeline D20, up to 3) or `runbook serve` refreshing at the same time could use an already-rotated token and lock the login out. The env-file write needs a lock and a re-read before refresh.
- R2. Slack: whether workspace admins must approve briar's app is not confirmed; Slack's PKCE switch marks the app as a public client and cannot be undone; the `xoxc` + `d` fallback rotates within hours on Enterprise Grid (community reports) and its terms-of-service status is not confirmed.
- R3. Pasted cookies expire and cannot be renewed by briar (L8 then stops the run). A `runbook serve` that uses one will stop when it expires.
- R4. GitHub through `gh` only: every host needs `gh` logged in with each company's account; per-account tokens need `gh auth token --user` (present in gh 2.69.0, the version on this machine; the minimum version is not checked).
- R5. The maintainer must register and keep briar's OAuth apps on Linear and Slack; a company that blocks them uses its own `client_id` (L12) or a cookie.
- R6. GCP per company: ADC is one per machine by default; a company needs its own gcloud configuration or credentials file. Scopes outside Google Cloud would need a briar Google client (not planned).
- R7. 1.x Bitbucket logins are already broken today (app passwords removed); 1.x users on Bitbucket cannot extract until 2.0 or a 1.x fix.

## Left for the spec

- The exact `auth:` block schema and env var names per company.
- The "who am I" call and the permission check per source.
- Whether 1.x gets a Bitbucket API token fix before 2.0 (R7).

## Evidence (2026-11-10)

- Today's logins: `src/briar/auth/_acquirers/` holds 9 (`aws-sso`, `aws-static`, `bitbucket-app-password`, `fireflies`, `github-device`, `github-pat`, `jira-session`, `jira-token`, `linear-api-key`); `CredentialAcquirer.refresh` in `src/briar/auth/_acquirer.py:100` raises for all of them. GitHub reads one `GITHUB_TOKEN` (`src/briar/env_vars.py:26`); Slack reads `SLACK_{c}_TOKEN` + `SLACK_{c}_COOKIE_D` with no login; GCP uses `google.auth.default()`, Azure `DefaultAzureCredential()`.
- `gh version 2.69.0 (2025-03-19)`; `gh auth token --help`: `-u, --user string   The account to output the token for`.
- GitHub device flow, no secret: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
- Jira 3LO is code grant only, secret required: https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/ ; scoped API tokens: https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/
- Bitbucket API tokens and git over HTTPS: https://support.atlassian.com/bitbucket-cloud/docs/using-api-tokens/ ; app password removal (creation blocked 2025-09-09, final removal set for 2026-07-28): https://www.atlassian.com/blog/bitbucket/bitbucket-cloud-enters-phase-2-of-app-password-deprecation , https://community.atlassian.com/forums/Bitbucket-articles/Deprecation-notice-Bitbucket-Cloud-app-password-brownout/ba-p/3237429
- Linear PKCE, secret optional, 24 h access, 30-day rotating refresh: https://linear.app/developers/oauth-2-0-authentication
- Slack PKCE since 2026-03-30, user tokens only on desktop redirects, 30-day refresh: https://docs.slack.dev/authentication/using-pkce , https://docs.slack.dev/changelog/2026/03/30/pkce
- Fireflies API key only: https://docs.fireflies.ai/fundamentals/authorization
- AWS SSO with `sso-session` refresh: https://docs.aws.amazon.com/sdkref/latest/guide/feature-sso-credentials.html
- Google desktop apps are public clients: https://developers.google.com/identity/protocols/oauth2/native-app
- Azure public client: https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.interactivebrowsercredential
- authlib 1.9.5 (2026-11-05, BSD-3, deps `cryptography`, `joserfc`): https://pypi.org/pypi/authlib/json
- The research was done by a subagent from official docs and PyPI; items it could not confirm are listed as risks above.
