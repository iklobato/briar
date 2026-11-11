# briar 2.0: the review routine of `briar serve` (spec 003 part 3)

Grill session, 2026-11-11, with the maintainer. Base:
`docs/decisions/2026-11-11-briar-2-serve-pipeline.md` (S1 to S35), the ticket
routine of spec 003 part 2 (https://github.com/iklobato/briar/pull/101), and
tasks T016 to T018 of the local spec plan.

Facts looked up before the questions:

- The finder of part 1 already lists the new comments of the PRs briar opened:
  inline review comments, Conversation comments and review bodies. It skips
  only the comments of the token account.
- `WorkBook.open_prs()` gives every ticket with a PR number, merged, closed
  and blocked ones too: 3 GitHub calls per PR per cycle, forever.
- Each GitHub comment has `author_association` (OWNER, MEMBER, COLLABORATOR,
  CONTRIBUTOR, NONE, ...) and `user.type` (`Bot`). On a public repo anyone can
  comment on a briar PR.
- `GET /pulls/{n}` gives `state`, `merged` and `mergeable_state` (`dirty` means
  a conflict with the base) in one call.
- The Claude Agent SDK (0.2.180) has `sandbox` for bash: writes only in the
  working folder, network only to `allowedDomains`. macOS uses its built-in
  sandbox; Linux needs `bwrap` and `socat` (this Mac has `socat`, not
  `bwrap`). Read limits are permission rules, not part of the sandbox.

## Decisions

### Whose comments

- V1. Comments briar acts on: people with write access (`author_association`
  OWNER, MEMBER or COLLABORATOR) and the bots listed in a new runbook key
  `review_bots` (logins such as `coderabbitai[bot]`). Every other comment is
  logged and left alone. Why: on a public repo a stranger could tell the agent
  to do things under the name of the token owner. Changes S22 ("people and
  bots"). (S22)
- V2. Own comments: every comment briar posts carries the hidden marker
  `<!-- briar -->`, and briar skips only marked comments. A comment of the
  token owner without the marker counts, so the owner can steer briar from the
  PR. Why: briar posts as the owner (S2). Replaces the "skip the token
  account" rule of part 1. (V1, S2)

### Which PRs and when

- V3. Watched PRs: only open ones whose ticket is not `blocked`. A merged or
  closed PR is recorded in the state and no longer read. A blocked PR is no
  longer read: its budget is spent; to retry, remove the ticket from the state
  file. Why: stop the 3 calls per PR per cycle on dead PRs, and no new spend
  without a new decision. (S17, S23)
- V4. A comment is new when its id was never answered, or when it was edited
  (`updated_at`) after briar answered it. A reply in a thread is a new
  comment. Why: the maintainer wants the thread followed. (V1)
- V5. Resolved review threads are skipped: briar reads the thread state with
  the GitHub GraphQL API (`reviewThreads { isResolved }`), one call per PR per
  cycle. briar does not resolve threads itself. Why: a resolved thread is
  closed by the people in it. (V4)
- V6. Conflict with the base: every cycle, a watched PR with
  `mergeable_state` `dirty` gets a round even with no new comment. briar
  merges `origin/<base>` into the branch (a merge commit, never a rebase or a
  force push), the agent resolves the conflict markers, and the usual checks
  run. Why: a PR in conflict cannot merge; reading `mergeable_state` costs no
  extra call (V3). (V3, S25)

### One review round

- V7. A round is one task over everything new on one PR at that moment: the
  base merge when there is a conflict (V6), then every new comment (V1, V4).
  One agent call reads the comments, the diff and the knowledge, edits, and
  answers per comment: fixed or not, and the reply text. (S18, S22)
- V8. Checks of a round: the verify loop, the self-review against the
  comments, push, then the CI wait with fixes, as in the ticket routine.
  Why: a fix must not break the build, and S20 applies to every push.
  (S20, V7)
- V9. Replies: an inline comment gets a reply in its thread; the Conversation
  comments and review bodies of the round get one comment that quotes each
  one. A fix names its commit; no change says why. Every reply carries the
  marker (V2). Why: less noise than one comment per comment, and the inline
  threads stay answered. Refines S22. (V2, V7)
- V10. The ticket (Jira or issue) gets no comment for a round; only for a
  limit (S23). (S23)

### Limits

- V11. `max_review_rounds`: a new required key of the `serve:` block, like
  the S24 limits. After that many rounds on one PR, briar stops: the PR
  becomes a draft with a report, the ticket gets the report, the ticket is
  `blocked`. A base merge round (V6) counts. Why: a bot that comments on every
  push would make rounds without end, each up to `max_usd`. Each round keeps
  its own `max_usd`, `max_loops` and `ci_wait_minutes` (S23). (S23, S24, V6)

### Safety of the agent (both routines)

- V12. Bash of the agent runs in the SDK sandbox: writes only in the
  worktree, no network. Read, Glob and Grep stay inside the worktree and the
  knowledge folder of the company (`ToolGate`). Applies to the ticket routine
  of part 2 too. Why: comments are text from other people (V1). Answers the
  first review question of https://github.com/iklobato/briar/pull/101.
  Adds to S25. (S25, V1)
- V13. `allowed_domains`: an optional runbook key, the domains the sandboxed
  bash may reach (for example `registry.npmjs.org`). None by default. The
  verify commands run in briar, outside the sandbox, and keep the network.
  (V12)
- V14. No sandbox support (Linux without `bwrap` or `socat`): the `serve`
  check and `doctor` fail and say what to install. No switch to turn the
  sandbox off. The Docker image installs both. (V12, S29, S35)

### Other

- V15. A task that fails before it starts (the clone, the ticket text), with
  nothing spent and nothing written, is logged and tried again on the next
  poll. Answers the second review question of #101; the code of part 2 stays.
- V16. Part 3 stays one PR (S31 kept: 4 PRs). It holds V1 to V15.

## Earlier decisions this file changes

- S22: only write-access people and listed bots (V1); replies in the thread
  for inline comments and one quoting comment for the rest (V9); edits and
  base conflicts start a round too (V4, V6).
- S23, S24: `max_review_rounds` joins the required limits (V11).
- S25: the bash sandbox and the read limit (V12 to V14).
- Part 1 finder: skips marked comments, not the token account (V2); reads
  only open, not blocked PRs (V3).

## Open risks

- V12: bash can still read any file of the user (only Read, Glob and Grep are
  limited), and a secret it reads can leave in a file of the PR diff. V1 cuts
  who can ask for that; a reviewer sees it in the diff.
- V13: every domain in `allowed_domains` is a way out for data.
- V14: the git of the worktree inside the sandbox is not tested yet (the
  worktree points to the `.git` of the clone, outside the worktree); the
  agent should not need git writes, since briar commits.
- V14: the SDK behavior when `bwrap` is missing (fail or run without sandbox)
  is not known yet; the check of V14 must not rely on it.
- V6: an agent that cannot resolve a conflict ends the round with the S23
  report; the merge commit may hold a bad resolution that only the checks
  of V8 catch.
- V5: the GraphQL call is a new API path with its own rate limit and errors.
- V1: a reviewer outside the write list (an outside contributor) gets no
  answer, only a log line.
- V11: the number of rounds has no measured default; each company sets it.
- V16: one PR with several themes (review, safety, conflicts) is a big diff
  to review.
