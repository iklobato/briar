# briar 2.0: the source logins, settled for spec 005

Grill session of 2026-11-10, after spec 006 (the core cleanup, PRs #91 to
#96). It settles what `docs/decisions/2026-11-10-briar-2-source-logins.md`
(L1 to L21) left for the spec, and the decisions that spec 006 changed
under it: `.briar.toml` now holds the `briar extract` settings (C1, C12,
E1 to E16), `briar auth` has only `login` and `status` (C15), and no 2.0
code reads Slack (the `slack-context` extractor left with spec 006). Not
reopened: the login per source (L1, L13 to L18), one account per source
per company (L2), secrets in `secrets.env` (L3), the CLI logins are read
and never copied (L3, L14, L15), `authlib` for OAuth (L19), renewing where
possible (L8).

Format: `ID. Decision: chosen answer. Why. (depends on)`

## Decisions

### Scope

- G1. Spec 005 covers the sources the 2.0 code reads today: GitHub (see
  G10 and G11), Jira and Bitbucket (Atlassian API token, pasted cookie), Linear (OAuth
  with PKCE and the API key), Fireflies (API key), AWS, GCP, Azure (CLI
  profiles), and the L4 read check. The Slack login (L16) and the write
  permission check (L5) move to spec 003, which is the first code that
  needs them. Why: nothing in 2.0 would use them before. (L4, L5, L16,
  ticket pipeline T6)

### Where the login settings live

- G2. The per-company login choices (`method`, the GitHub `host` and
  `client_id`, the AWS `profile`, the gcloud `config_dir` (G26), the Azure
  `tenant`, the Linear `client_id`) live in the config: `[auth.<source>]` in `.briar.toml` for
  `briar extract`, and `companies.<company>.auth.<source>` in the runbook
  for `briar runbook`. Same keys in both. Changes L7, which named the
  runbook only. Accepted cost: a committed `.briar.toml` holds the profile
  or folder name of whoever wrote it. (L7, C1)
- G3. `briar runbook` reads the login choices from the runbook only, never
  from a `.briar.toml` next to it. Why: a runbook runs on a server by
  itself, and one rule is easier to follow than a merge. (G2)
- G4. The non-secret values that 1.x kept in env vars move to the config:
  `[auth.jira] site` (was `JIRA_<C>_URL`) and `[auth.bitbucket] workspace`
  (was `BITBUCKET_<C>_WORKSPACE`). Secrets stay in `secrets.env`. (G2, L3)
- G5. A source with more than one method picks it with `method` in its
  `[auth.<source>]` block (`"token"` or `"cookie"` for Jira and
  Bitbucket, `"oauth"` or `"api-key"` for Linear, `"device"` or
  `"token"` for GitHub). `briar auth login` and every run read it; there
  is no `--method` flag. With no `method`, the default applies (Jira and
  Bitbucket: token; Linear: OAuth; GitHub: device).
  Accepted cost: the person edits the config before a non-default login,
  and the committed file sets the method for the whole team. (G2, L1, L13,
  L17)
- G6. `briar auth login <source> --company C` and `briar auth status` read
  `.briar.toml`; with `--runbook FILE` they read `companies.C.auth` from
  that runbook instead. Why: a server has a runbook and no `.briar.toml`,
  and a file path is a value that changes per run (C1). (G2, G3)

### Names

- G7. The env vars briar reads and `briar auth login` writes are
  `BRIAR_<COMPANY>_<SOURCE>_<FIELD>` (for example `BRIAR_ACME_JIRA_TOKEN`,
  `BRIAR_ACME_JIRA_COOKIE`, `BRIAR_ACME_LINEAR_API_KEY`). Every 1.x user
  logs in again. Why: one prefix, easy to find. (L3)
- G8. When the 2.0 name of a credential is missing and its 1.x name is
  set (`JIRA_ACME_TOKEN`, `LINEAR_ACME_TOKEN`, `FIREFLIES_ACME_API_KEY`,
  `AWS_ACME_ACCESS_KEY_ID`, `BITBUCKET_ACME_APP_PASSWORD`, ...), the run
  stops with exit 1 and names both: rename it, or run
  `briar auth login <source> --company <company>`. Same style as the other
  2.0 guards. (G7)
- G9. The targets of `briar auth login` and `briar auth status` are the
  source names: `github`, `jira`, `bitbucket`, `linear`, `fireflies`,
  `aws`, `gcp`, `azure`. A 1.x target name (`github-pat`, `github-device`,
  `jira-token`, `jira-session`, `aws-sso`, `aws-static`,
  `bitbucket-app-password`, `linear-api-key`) gets the usual usage error
  (exit 2) with the valid list; the CHANGELOG maps old to new. (G5, C22)

### GitHub

- G10. GitHub logs in with the device flow of a GitHub App of briar
  (asked again after the first round, which had kept gh only): `briar auth
  login github --company C` shows a code, the user approves it in the
  browser with the account for that company, and briar keeps the access
  token, the refresh token and the expiry in `secrets.env`
  (`BRIAR_<C>_GITHUB_*`). briar renews the token by itself: it lasts 8
  hours, the refresh token 6 months, and the device flow needs no client
  secret, not even to renew. Each company org installs the app. Changes
  L15 (gh only) and the first G10. (L1, L2, L8, L12)
- G11. GitHub also takes `method = "token"`: a pasted fine-grained PAT per
  company (`BRIAR_<C>_GITHUB_TOKEN`). No gh method and no company-owned
  app. Changes the "no pasted PAT" of L15. (G10, L10)
- G22. GitHub library: `httpx` for the device flow and the refresh (the
  1.x `auth/_acquirers/github_device.py` already does the flow), and
  PyGithub 2.9.1, already a dependency, with `Auth.Token` for the API. No
  new dependency; githubkit is not used. (G10)
- G23. The maintainer registers the GitHub App of briar before the GitHub
  PR, and its public `client_id` goes in the code. A company that needs
  its own app sets `client_id` in `[auth.github]` (L12); so does a GitHub
  Enterprise `host`. (G10, L12)
- G24. The app asks for read permissions only. Spec 003 adds the write
  permissions it needs, and each org approves the change then. (L5, G10)
- G25. `GITHUB_TOKEN` set while the company has no 2.0 GitHub login stops
  the run with exit 1, as in G8: use `method = "token"` and
  `BRIAR_<C>_GITHUB_TOKEN`. briar no longer reads `GITHUB_TOKEN`. (G8,
  G11)

### The check before a run

- G12. `briar extract` and every runbook task check every source the run
  uses. A source with no login, or a login that fails the check (expired,
  wrong account, no permission), stops the run with exit 1. Changes the
  1.x behavior, where an extractor without a login was skipped. Accepted
  cost: `briar extract` with no `--include` and no `[extract] include`
  needs a login for every source the 23 extractors use with their
  defaults (GitHub, Jira, AWS, Fireflies). (L4, L8, K10)
- G13. When the check stops a run, it lists every source that failed, each
  with its fix command, and then exits 1. Why: fix them all in one go.
  (G12)
- G14. `briar auth status <source>` and `briar doctor` both run the check
  live (a "who am I" call per source of the company). (L4, C16)
- G15. In `briar doctor`, a login the API rejects (401, 403) is `fail`
  (exit 1, as the run would stop); a network error is `warn` (exit 0), so
  `doctor` still works offline and in a CI with no access. (G14)

### Code

- G16. The `CredentialStore` interface and its one-entry registry go; the
  env-file class is used directly. Why: one implementation needs no
  interface; it can come back with a second store. Closes F1. (F1, L3)
- G17. Linear OAuth ships without a briar app: `[auth.linear] client_id`
  is required when `method = "oauth"`. When the maintainer registers
  the Linear app of briar, a small PR puts its public `client_id` in the code as
  the default. Why: nothing waits on the registration. Changes the timing
  of L12. (L12, L17, R5)
- G18. The refresh of a Linear or GitHub token takes a lock on
  `secrets.env` and reads it again before refreshing, so two runs never
  use an old rotated token (R1; a GitHub refresh also returns a new
  refresh token). (G10, G17, L19)
- G26. GCP per company is a gcloud config folder, `[auth.gcp] config_dir`,
  not a named gcloud configuration as G2 first said: the ADC file that the
  Google libraries read lives in `$CLOUDSDK_CONFIG` (google-auth
  `_cloud_sdk.py` `get_config_path`), and a named configuration does not
  separate it. `briar auth login gcp` runs
  `CLOUDSDK_CONFIG=<dir> gcloud auth application-default login`. (G2, L14)

### Plan

- G19. Four stacked PRs into `v2`, each with its manual and CHANGELOG
  lines: (1) the `[auth]` block and `companies.<c>.auth`, the `BRIAR_*`
  names and the 1.x name guard (G8, G25), the flat store, `--runbook`, the
  source targets, and the pasted logins (Jira and Bitbucket token and
  cookie, Fireflies key, Linear key, GitHub PAT), with the adapters reading
  the new names and the 1.x acquirers removed; until PR 4, GitHub works
  with `method = "token"` only; (2) AWS, gcloud and Azure; (3) the check of
  G12 to G15 in `extract`, `runbook`, `auth status` and `doctor`; (4) the
  GitHub device flow (G10, G22 to G24) and Linear OAuth (G17), the
  refresh lock (G18), the docs and the success checks. The renewable
  tokens live in one PR, and the GitHub App is registered before it. (G1,
  G10)
- G20. Before each merge, the logins are tested for real, read only (the
  "who am I" call and a small extract), on the GitHub, AWS, Jira or
  Bitbucket, Linear, Fireflies, GCP and Azure accounts of the maintainer.
  The maintainer runs every `briar auth login` that asks for a pasted value;
  nobody else reads a secret value. Anything not tested this way is named
  in the PR. (G19)
- G21. 1.x gets no Bitbucket fix (R7). Its login has not worked since the
  app passwords left; the 2.0 CHANGELOG says so, and Bitbucket users wait
  for 2.0. (L13, R7)

## Changes to earlier decisions

- L4: the check also runs in `auth status` and `doctor` (G14), and a
  missing login stops the run instead of skipping (G12).
- L5 and L16: move to spec 003 (G1).
- L7: the login block also lives in `.briar.toml` (`[auth]`) for
  `briar extract` (G2).
- L15: GitHub logs in with the device flow of a briar GitHub App, or a
  pasted PAT; not through gh (G10, G11). D33 and the Dockerfile note that
  GitHub logins read `gh auth token` no longer hold.
- L12: the Linear app of briar comes later; `client_id` is required until then
  (G17).
- The env var names of 1.x (`<SOURCE>_<COMPANY>_<FIELD>`, `GITHUB_TOKEN`)
  are replaced (G7, G8, G25).

## Open risks and things left undecided

- G2 and G5 put per-person values (profile, folder, method) in a
  committed `.briar.toml`; teammates with different accounts each edit it,
  or keep it out of git.
- G12 makes a bare `briar extract` stop for a user who has only GitHub;
  the README and the quickstart must use `--include` or `[extract]
  include`.
- G14 adds network calls to `briar doctor`; G15 keeps it at exit 0 offline,
  but it gets slower.
- G10: each company org must install the GitHub App of briar (an org
  admin approves it); a token only reaches repos where the app is
  installed, so the check must say which org lacks it. A GitHub Enterprise
  host needs its own app (G23).
- G23: the GitHub PR waits until the maintainer registers the app.
- G17: until the Linear app of briar exists, every Linear OAuth user registers
  their own app.
- R2 (Slack app approval) and R3 (pasted cookies expire) are unchanged; R2
  moves to spec 003 with Slack.
- How 2.0 is released is still open (status file, section 5, item 2).

## Evidence (2026-11-10, this machine)

- GitHub App user tokens (https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens):
  access token 8 hours, refresh token 6 months; on refresh, `client_secret`
  is "Required unless the user access token was generated using the device
  flow".
- PyGithub 2.9.1 (`uv.lock`) `github.Auth`: `Token`, `AppAuth`,
  `AppInstallationAuth`, `AppUserAuth` (takes a `client_secret`), no device
  flow. githubkit 0.17.1 (2026-11-06, needs `anyio`, `hishel`,
  `githubkit-schemas`) has `OAuthDeviceAuthStrategy` and refresh without a
  secret. `ghtoken` 0.1.2 (2024) only runs `gh auth token` with
  `subprocess`, with no `--user`.
- gh keeps its token in the OS keyring, service `gh:<host>`, account
  `<user>` (cli/cli `internal/config/config.go:746`, zalando/go-keyring
  v0.2.8), and newer gh renews refreshable tokens under its own lock
  (`config.go:258`); reading the keyring directly would skip that.
- `GH_TOKEN=<fake> gh auth token` printed the fake value (exit 0);
  `GITHUB_TOKEN=<fake> gh auth token --hostname github.com` (no `GH_TOKEN`)
  printed it too; `GH_TOKEN=<fake> gh auth token --user someone` printed
  `no oauth token found for github.com account someone`.
- `gh release view v2.40.0 -R cli/cli`: "Support for multiple accounts on a
  single host" (https://github.com/cli/cli/pull/8425); the v2.46.0 notes
  only clarify the `--user` help text. Latest release: `v2.103.3`
  (2026-10-26). This machine: gh 2.69.0.
- `grep -rln -i slack src/briar`: only `src/briar/telemetry/_scrubber.py`.
- `src/briar/env_vars.py`: the 1.x names (`JIRA_{c}_TOKEN`,
  `JIRA_{c}_URL`, `BITBUCKET_{c}_APP_PASSWORD`, `BITBUCKET_{c}_WORKSPACE`,
  `LINEAR_{c}_TOKEN`, `FIREFLIES_{c}_API_KEY`, `AWS_{c}_ACCESS_KEY_ID`, the
  global `GITHUB_TOKEN`).
- `src/briar/auth/_acquirers/`: the 9 acquirers of 1.x, still on `v2`.
- `src/briar/credentials/__init__.py:22`: `CredentialStoreRegistry`, one
  entry (`envfile`).
