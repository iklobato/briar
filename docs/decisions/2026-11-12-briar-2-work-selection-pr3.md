# briar 2.0 spec 008 PR 3: the GitLab code host (extract and serve)

Grill of 2026-11-12, after PR 1 (https://github.com/iklobato/briar/pull/105) and PR 2
(https://github.com/iklobato/briar/pull/106). It builds on N12, N13, N14 and N16 of
`2026-11-12-briar-2-work-selection.md`.

Facts found before the grill (code at `select/02-chat` @ `92214fc`):

- `serve` builds its code host with `make_provider("github")` (`src/briar/serve/work.py`), and
  `WorkSources.github()` returns `Any`: the 12 methods `serve` calls (`open_pull`, `comment`,
  `make_draft`, `make_ready`, `ci_state`, `pull_state`, `branch_head`, `branch_pull`,
  `review_comments`, `resolved_comment_ids`, `reply_in_thread`, `can_write`) exist only on
  `GithubProvider`, with no declared contract.
- GitHub is fixed in `serve` in more places: `https://github.com/.../pull/N` in log lines
  (`flow.py`, `ticket.py`, `routine.py`), `HOST` and `x-access-token` in `git.py`, the noreply
  email of `GitIdentity`, `author_association` in `ReviewFilter`, the `gh ` block in `agent.py`.
- Repo names allow two parts only (`REPO_NAME` in `models.py`, and the same regex in
  `github.py`, `_gh.py`, `infer.py`). The repos of a real company on GitLab are 4 to 6 levels
  deep (`group/sub/.../repo`).
- `briar extract` picks a code host by `provider` (`[extract]`, `[extract.<step>]`,
  `--provider`); Bitbucket implements 13 read methods and none of `serve`.
- Two tests assert that `gitlab` is an invalid provider choice
  (`tests/unit/commands/test_extract_config.py`, `tests/unit/commands/test_extract_params.py`).

| # | Decision | Answer | Why | Depends on |
|---|---|---|---|---|
| G1 | How a runbook says its repos are on GitLab | One host per company: `serve: {code_host: gitlab}`, default `github` | simplest; a company with repos on both hosts is not supported | N12 |
| G2 | Repo names on GitLab | With `code_host: gitlab`, a repo is a full project path of any depth (`group/sub/repo`); GitHub keeps `owner/repo` | real company paths are up to 6 levels deep | G1 |
| G3 | How briar talks to the GitLab API | The `python-gitlab` library (8.6.0, LGPL-3.0-or-later, adds `requests-toolbelt`) | it covers the whole API and its pagination; the maintainer kept it after seeing the license | N13 |
| G4 | Which `extract` steps work on GitLab | Every step with a GitLab equivalent: merge requests, MR notes, pipelines, commits, `read_file`, tree, code search, releases, protected branches, environments, deployments. Dependabot and code scanning have no free GitLab equivalent and return nothing, as on Bitbucket | the maintainer wants extract on GitLab as complete as GitHub where the API allows | G3 |
| G5 | How `extract` picks GitLab | The `provider = "gitlab"` key that exists today (`[extract]`, `[extract.<step>]`, `--provider`); `serve.code_host` is for `serve` only | no change to how extract is set up | G1 |
| G6 | The contract of `serve` with a code host | A `CodeHost` Protocol with the methods `serve` calls; `GithubProvider` and the new `GitlabProvider` implement it, and `WorkSources` returns it typed | mypy catches a missing method before a run does | N14 |
| G7 | Whose MR review comments briar answers | Project members with access level Developer (30) or above | the same as "can push" on GitHub (`OWNER`, `MEMBER`, `COLLABORATOR`) | G6 |
| G8 | Pipeline status of the MR | `success` passed; `failed` and `canceled` failed; `skipped` and a pipeline stopped at a `manual` job passed; `created`, `pending`, `running` and the other open states pending; no pipeline none | a canceled pipeline needs a look; skipped or manual is not a code error, the MR goes on | G6 |
| G9 | Email of the commits briar makes on GitLab | The `commit_email` of the GitLab profile of the token owner (`/user`), else its main email | GitLab links the commit to the user; the profile setting is the one the user chose for commits | G6 |
| G10 | `glab` in the bash of the agent | Blocked, as `gh` is | every write to GitLab goes through briar (log, budget, review) | N14 |
| G11 | `review_bots` on GitLab | Any GitLab username; the `name[bot]` rule stays for GitHub only | GitLab bot users have no fixed name pattern | G7 |
| G12 | Real proof, read only | On a real company GitLab: the login check and the GitLab `extract` steps on the two projects with the most MRs of the token owner; nothing written; texts may go to the model as in the extract | real data at a real size, many review notes and pipelines | G4 |
| G13 | Token of the G12 proof | Changed while building 3a: a new personal access token with `read_api` only, saved by the maintainer in `secrets.env` as `BRIAR_<COMPANY>_GITLAB_TOKEN` and never printed. First answer was the token the `glab` CLI holds; that login is OAuth2 (kept in the OS keychain, sent as `Authorization: Bearer`, expires), and briar sends `PRIVATE-TOKEN` | the proof runs the real login path, and GitLab itself refuses any write | G12 |
| G14 | Real proof with writes | A personal gitlab.com account of the maintainer with a private test project, and a free Jira Cloud site of the maintainer as the tracker: a draft MR, a review round and a pipeline wait | the whole path (Jira ticket to GitLab MR) with no company system written | N16 |
| G15 | How PR 3 is split | PR 3a: GitLab login, provider and `extract` (base `v2`, independent of PR 1 and PR 2). PR 3b: `serve` on GitLab (G1, G2, G6 to G11, G16), stacked on 3a and PR 2 | each part is reviewable; 3a is proved read only before any write exists | G4, G6 |
| G16 | `code_host: gitlab` with `serve.repos` set | Refused when the runbook loads, with an error that says GitLab tickets come from `jira.<PROJ>.repos` only | GitLab Issues are out (N12); nothing runs half configured | G1 |
| G17 | Passing GitLab errors (429, 5xx, timeout) | `obey_rate_limit` and `retry_transient_errors` of `python-gitlab`, a few tries, then stop as C7 and C8 of PR 2 do; a revoked token or a missing scope stops at once | a short rate limit must not stop a run | G3 |
| G18 | GitLab login (from N13) | `BRIAR_<COMPANY>_GITLAB_TOKEN`, `[auth.gitlab] url` (default `https://gitlab.com`); the check calls `/user` and reads each project; the write check of `serve` needs access level 30 or above | same shape as the other logins | N13 |
| G19 | PR 3a base branch | `v2` directly | it does not touch the selector or Slack, so it can merge before #105 and #106 | G15 |
| G20 | GitLab Issues | Still out (N12); the test tracker of G14 is Jira | keeps PR 3 to the code host | N12 |

Follow from the decisions above, no choice left (checked against the GitLab API docs when
built, not yet verified live):

- Clone and push over HTTPS with the token and the username `oauth2`.
- A draft MR is the `draft` flag (the `Draft:` title prefix on older servers); "mark ready"
  clears it.
- Merge conflict from `has_conflicts` / `detailed_merge_status`.
- Links in logs and comments come from the `web_url` of the API, never built by hand.
- On GitLab the agent prompt says "merge request" and points at `.gitlab-ci.yml`.
- The `<!-- briar -->` marker works in GitLab notes (Markdown hides HTML comments).

## Open risks

- G3: LGPL-3.0 dependency in an MIT project. Fine as an unmodified dependency; anyone who
  ships a bundle (a zipapp, a frozen binary) must keep the LGPL terms for that part.
- An OAuth2 login (as `glab` keeps) is not a GitLab login method of briar; only access tokens.
- G4: code search on gitlab.com may need advanced search on the plan of the project; not
  verified. A 403 or empty answer returns nothing, as on Bitbucket.
- G8: merged results pipelines and pipelines of a fork are not looked at separately.
- Self-hosted GitLab with a private CA certificate is not handled (no setting for a CA file).
- G1: a company with repos on GitHub and GitLab cannot be served by one company entry.

## Not decided here

- GitLab Issues as a tracker (N12 keeps them out).
- PR 4 (repo choice and the open PR/MR rule, N6) gets its own grill.
