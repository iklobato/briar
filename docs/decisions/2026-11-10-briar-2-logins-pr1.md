# briar 2.0: spec 005 PR 1, the config, the names and the pasted logins

Grill session of 2026-11-10 for the first PR of spec 005 (tasks T001 to
T008 of the local `specs/005-source-logins/tasks.md`). It settles what PR
1 needs on top of `docs/decisions/2026-11-10-briar-2-logins-spec.md` (G1
to G26), which is not reopened.

Format: `ID. Decision: chosen answer. Why. (depends on)`

## Decisions

- H1. A missing required value (`site` under `[auth.jira]`, `workspace`
  under `[auth.bitbucket]`) stops `briar auth login` and the run with exit
  1 and says what to add where. briar never writes `.briar.toml`; the
  `briar init` template gets a commented `[auth]` example. Why: the file
  is committed and belongs to the team. (G2, G4)
- H2. Bitbucket keeps the workspace and repository access tokens of 1.x
  (Bearer, no email; 1.x used the user name `x-token-auth`) as
  `method = "access-token"`, secret `BRIAR_<C>_BITBUCKET_ACCESS_TOKEN`.
  Why: the only Bitbucket login that is not a person, good for a server,
  and the code exists. (G5, L13)
- H3. The Bitbucket cookie method is built and tested with fakes only, no
  real run before merge. Changes G20 for this one method: the PR names it
  as not tested for real, since it is not proven that the Bitbucket REST
  API takes a browser session cookie (research R-4). (L13, G20)
- H4. No migration help: `briar auth login` does not offer to copy a 1.x
  value; the user enters it again and the G8 guard names the new place.
  (G7, G8)
- H5. `[auth.jira] user_agent` (optional) replaces `JIRA_<C>_USER_AGENT`:
  the User-Agent the cookie method sends, for a Jira that checks it
  against the browser that made the cookie. (L9, G4)
- H6. Every command that reads `[auth]` (`extract`, `auth`, `doctor`)
  checks the whole block, every source in it, even the ones the run does
  not use, and stops with exit 1 naming the file and key. Same as
  `[extract]`. (G2)

## What follows from earlier decisions (no new choice)

- H7. The G8 and G25 guard runs when a run resolves a login (`extract`,
  `runbook`, `doctor`), never in `briar auth login`, which is how the
  user fixes it. `briar auth status <source>` shows the same hint. (G8)
- H8. Until PR 4 brings the device flow, a GitHub block with no `method`
  means `token`. (G19)
- H9. In PR 1, `briar auth login` saves the values without the live
  check; PR 3 adds it. (G19)
- H10. `site` takes `acme.atlassian.net` or `https://acme.atlassian.net`.

## Open risks

- H3: the Bitbucket cookie may not work against the real API.
- H5 adds a key that may stay unused.
