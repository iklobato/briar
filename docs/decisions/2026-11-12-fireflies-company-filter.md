# Fireflies: which meetings belong to a company

Date: 2026-11-12. Grill with the maintainer after the validation run of `briar extract`.

## The problem

One Fireflies key can be a personal account that records the meetings of several companies.
Today `meeting-digest` keeps every meeting the key can see, or the ones with one of the exact
emails of `attendee_allow` (a filter Fireflies runs on its side).

Facts, read only, on a personal account with meetings of several companies (the 50 newest
meetings, 2026-11-12):

- every meeting had 1 participant email, the owner of the account (a public mail domain);
- every meeting link was the same video host, so the link tells nothing;
- every meeting had a title, but many titles are a meeting code (`abc-defg-hij`);
- speaker names came with some meetings and not with most (0 speakers in most rows).

So on such an account an email filter keeps nothing useful; the title and the speakers are
the only signals.

## Decisions

| # | Decision | Answer | Why | Depends on |
|---|---|---|---|---|
| F1 | Signals that tie a meeting to the company | title words, speaker names, attendee email domain; a meeting is kept when any one matches (OR) | each signal works on a different kind of account | |
| F2 | Title match | case-insensitive substring of a runbook list | simple to write; a close title of another company can match | F1 |
| F3 | Where speaker names come from | the member APIs of the sources of the company: Jira project users, GitLab group members, Slack channel members of the runbook | does not depend on the order of the sources | F1 |
| F4 | Speaker name match | full name, case and accents ignored (`Bo Wang` = `bo wang`) | few false matches; nicknames from the video host do not match | F3 |
| F5 | The owner of the key | left out of the members: each source drops the account of its own login (changed 2026-11-13; first answer: counts like anyone else) | a real run kept 2 of 19 meetings of another client where only the owner of the key spoke | F3 |
| F6 | A member call fails (no permission, source down) | the Fireflies source stops for that run, with the reason | safer than mixing companies | F3 |
| F7 | `attendee_allow` | one list that takes an exact email or `@domain`, matched by briar on attendees and the organizer (built: exact emails are no longer sent to Fireflies, whose filter would make it AND with the title, not OR) | one field, no break for current runbooks | F1 |
| F8 | Filtering runs in briar, Fireflies gives 50 per page | read more pages until the company `max_count` is kept or the date window ends | the first page can be all other companies | F1 |
| F9 | Meetings with no title word, no member speaker and no matching email | dropped when a filter is set | follows from F1 | F1 |
| F10 | No filter set | as today, every meeting is kept | no break | |
| F12 | When the members are fetched | on every run of a company that has a member source (Jira, GitLab or Slack login) | maintainer choice, asked after the grill; see R1 | F3 |
| F13 | How many PRs | one, on top of the Slack PR and the GitLab extract PR | maintainer choice | |
| F3b | Member sources in practice | the users of the Jira site, the members of the GitLab groups of the token, the users of the Slack workspace (no bots, no deleted) | extract has no Slack channels (they are under `serve:`) and no Jira project key; approved with the plan | F3 |
| F3c | Jira people with a plain account | the people who can be assigned in `jira_projects` (`user/assignable/search`); the whole site only when no project is set | a real run, 2026-11-13: `users/search` answered 403 `error.no-permission` for a plain account, `user/assignable/search?project=...` worked | F3b |
| F10b | What "no filter" means with F12 | no title words, no `attendee_allow`, and no member source | F12 turns the speaker signal on whenever a member source exists | F10, F12 |
| F11 | Warning when the key is shared | `briar doctor` and `briar extract` warn when the same Fireflies key is set for 2 or more companies and one of them has no filter; the key value is never shown | sure signal when several companies live on one machine | F10 |

## Open risks

- R1 (from F5, closed 2026-11-13): with the owner counted, a real run kept 2 of 19 meetings of
  another client (only the owner spoke). F5 now leaves the owner out; a meeting of the owner
  alone with someone outside the members then needs a title word.
- R2 (from F4): speaker names from the video host often differ from the account name
  (nicknames, initials): such meetings are dropped unless the title matches.
- R3 (from F8): one more page per 50 meetings of other companies in the window; the cost of
  a run grows with the meetings of the whole account, not of the company.

## Left undecided

- A page cap for F8, to be set from a measured run, not guessed.

## Names

`--meeting-title-any` (runbook arg `meeting_title_any`, `.briar.toml` key `title_any`), and
`--meeting-attendee-allow` keeps its names.
