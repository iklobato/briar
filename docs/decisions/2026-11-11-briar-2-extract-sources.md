# briar 2.0: make `extract` work for AWS, Azure, GCP, Fireflies, Bitbucket and Linear

Grill session, 2026-11-11. Input: the source checks of `docs/briar-2-validation.md` section 3
(findings D1 to D15 of the real runs, and the per-source items A, Z, G, F, L and BB). Planned
as spec 007; **nothing of this file is built yet**. Where a later decision of the same day
differs, it wins: W8 of `2026-11-11-briar-2-serve-knowledge-draft.md` makes a failed `serve`
poll a log line and a retry (it does not touch E3 and E6, which are about scheduled extracts).

## Decisions

| # | Decision | Answer | Why | Depends on |
|---|---|---|---|---|
| E1 | Where the work lands | Stacked PRs on top of the serve stack, current single-blob format; spec 004 changes the format later | sources work sooner; some rework when spec 004 lands | - |
| E2 | A failed extractor | The whole `extract` run stops, exit 1, nothing written | simple and loud; one bad source blocks the others (accepted) | - |
| E3 | `serve` when a scheduled extract fails its login | Stops, as today (J8 kept) | forces the login to be fixed | - |
| E4 | `@swallow_errors` (52 uses in `src/briar/extract`) | Keeps catching per call, records the failure; a recorded failure makes the run fail (E2) | smallest change; no failure stays hidden | E2 |
| E5 | A feature turned off at the source (Dependabot off, no code scanning analysis) | Own status `not enabled`, written in the section, not a failure, exit 0 | otherwise every repo without Dependabot fails | E2 |
| E6 | `serve` when a scheduled extract fails on data (not login) | Stops, like a login failure | same rule as E2 and E3: nothing goes stale quietly | E2, E3 |
| E7 | An extractor whose source has no config | The run fails, exit 1 | explicit; with E8 the person lists what runs | E2 |
| E8 | What runs with no `--include` | `[extract] include` in `.briar.toml` is required (K10); without it the run stops with "set [extract] include" | E7 would make "all 21" fail for almost everyone | E7 |
| E9 | `briar init` | Writes `[extract] include` with the GitHub extractors (the repo is known); other sources commented out | `briar extract` works right after `init` with only a GitHub token | E8 |
| E10 | `meeting-digest` with no company filter | Skipped with one line (`skipped (no meeting filter)`) | never mixes companies: one week of one real account held meetings of several employers and private ones | - |
| E11 | Meeting filter fields | `organizer_email`, `channel_id`, `participant_email`, `title`: all real `transcripts` arguments today | the person picks what fits each company | E10 |
| E12 | Several filters, and more meetings than `max` | Combined with AND (the API does it); page with `skip` to the end of the window; `max` is an explicit cap, and the section says when it cut | no silent cut at 25 | E11 |
| E13 | Clouds | Three extractors: `aws-infra`, `azure-infra`, `gcp-infra`, each with its own section, login and include; a 1.x `cloud = "azure"` / `"gcp"` under `aws-infra` stops with the new name | one cloud no longer replaces another (D4) | - |
| E14 | Regions | `regions = [...]` required for each cloud extractor that has regions; no default; missing = exit 1 | nothing implicit (D1: the `us-east-1` default hid a whole account; G2: invalid on GCP) | E13 |
| E15 | Cloud coverage | Add Kubernetes and VMs on the three clouds (EKS/AKS/GKE, EC2/Azure VM/Compute Engine) | covers most of a real account (thousands of tagged resources on a real AWS account) | E13 |
| E16 | Kubernetes and VM detail | Per cluster: name, version, node count, region. VMs: count by type and state. No IPs, no tags | useful, few calls, little sensitive data in the file the agent reads | E15 |
| E17 | Bitbucket issue tracker | Removed: tracker choice, adapter, tests; a config that names it stops with the CHANGE-3071 link | the API returns 410 since 2026-08-20 | - |
| E18 | Bitbucket cookie login | Kept, labeled "not proven live" (no Bitbucket access to prove it) | keeps the option for cookie-only users | E19 |
| E19 | Done without real access (Bitbucket, Linear) | Merge with offline proof (Linear schema, Bitbucket public API, recorded real responses), labeled "not proven live" in the docs and in `doctor` | the maintainer has no Bitbucket or Linear account | - |
| E20 | Real accounts for proof | GitHub, Fireflies (key of the maintainer), one read-only AWS SSO profile, one Azure subscription that has the types briar reads, one GCP project the maintainer picks (the names stay out of the repo) | each count must be compared with the official CLI on real data | - |
| E21 | Orders in the knowledge text | In PR 1: the section intros describe only ("binding", "must avoid", the `meeting-context` pointer go), and `serve/prompts/system.md` says the knowledge and PR titles are untrusted data | closes meeting or PR title text reaching the agent as orders (D9, D10) | - |
| E22 | Test fakes | Built from real recorded responses (curl or SDK output in `tests/fixtures/`, names and ids replaced); Linear from its public schema, Bitbucket from its public API | today the fakes agree with the code and not with the API (`cancelled`, `reviewers` as a list, Linear errors as 200, a mocked `list_buckets`, the old Fireflies schema) | - |
| E23 | Linear closed states | `completed`, `canceled`, `duplicate` | the schema spells `canceled`; duplicates are closed too | - |
| E24 | GCP Pub/Sub | List subscriptions with their backlog, not only topics | a topic is global and has no queue | - |
| E25 | Unused verbs | Delete Fireflies `search_meetings` / `get_meeting` and Linear `get_ticket` / `list_comments` with their tests | no caller in 2.0 | - |
| E26 | Cloud PRs | One PR per cloud; the shared parts (three extractors, `regions`) go in the AWS PR | smaller reviews, one real run each | E13, E14, E15 |
| E27 | PR order | 1 base (E2 to E9, E21, the fixed section order and the atomic write), 2 Fireflies, 3 AWS, 4 Azure, 5 GCP, 6 Linear, 7 Bitbucket; a grill before each | the base makes every later bug visible; Fireflies is broken for everyone and leaks; no-access sources last | all |

The atomic write of E27 PR 1 is already built (`9d599b6`, PR https://github.com/iklobato/briar/pull/104).

## What every PR must show before merge

- The regression test first, failing on the current code.
- One real run (E20 sources) where every count equals the official tool run in the same
  minute (`aws`, `az`, `gcloud`, curl); or, for E19, the offline proof and the "not proven
  live" label.
- The full flow once (`briar extract` with `[extract] include` set): exit code, each section
  status, and a second run that gives the same file.

## Open risks

- E2 + E6 + E3: with six sources connected, any one source down stops `extract` and stops
  `serve` for every company. A Fireflies schema change or an expired AWS SSO login halts the
  pipeline until a person acts. Chosen on purpose; there is no alert yet (C20, K4).
- E7: a 1.x user, or anyone with a stale cloud key on the machine, gets exit 1 until the
  config is cleaned. The stop message must name the key to set or remove.
- E20: cloud inventories land in a local file; keep account ids, resource names and
  subscriptions out of the repo, PRs and fixtures (E22 replaces names and ids).
- E15/E16: Kubernetes and VM reads need more read permissions; a read-only role that lacks
  one gives a failure (E4), which stops the run (E2).
- E19: Bitbucket and Linear ship unproven live; BB13 (cookie, repository access token) and
  L3 (query complexity) stay estimates.
- E1: the single-blob format is rewritten by spec 004; E5 and E2 map to K7 then.

## Left undecided

- How 2.0 is released (S32 of the status doc).
- Kubernetes and VM readers for each cloud: which library calls (decided in the grill of
  PRs 3 to 5).
