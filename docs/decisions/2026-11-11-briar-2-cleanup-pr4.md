# briar 2.0: `auth` and `runbook` leave (spec 003 part 4)

Grill session, 2026-11-11, with the maintainer. Base: S6, S7, S31 and S35 of
`docs/decisions/2026-11-11-briar-2-serve-pipeline.md`, and parts 1 to 3 of spec 003
(https://github.com/iklobato/briar/pull/100 to https://github.com/iklobato/briar/pull/102).

Facts looked up before the questions:

- `briar init` writes only `.briar.toml` (from `init_templates/briar_toml.tmpl`).
- `briar auth login` asks for a secret in the terminal and writes it to `secrets.env`
  (`EnvFileStore.write`); `briar auth status` runs the login check, for `.briar.toml` or
  for a runbook with `--runbook`.
- `briar runbook` has `extract` (one runbook, once) and `serve` (the scheduled
  extracts of a folder).
- About ten hints in the code tell the reader to run `briar auth login ...`.
- `briar serve DIR` stops with an error when no company of the folder has a `serve:`
  block, so a folder with only scheduled extracts has no daemon once `runbook serve`
  leaves.
- The commands 2.0 already removed (`dashboard`, `chat`, ...) give the Typer error
  `No such command`.
- `briar` reads secrets from the environment and from `secrets.env`
  (`$BRIAR_SECRETS_FILE`, else the XDG path); that stays.

## Decisions

- X1. A folder where no company has a `serve:` block: `briar serve DIR` says so in one
  log line and runs only the scheduled extracts, as `briar runbook serve` did. Why: the
  extract-only users keep their daemon. Changes part 1 (it stopped with an error).
  (S7)
- X2. `briar doctor --runbooks DIR`: loads the runbooks (the required keys), then runs
  the checks `serve` runs before its first cycle (model login, agent sandbox, read and
  write logins of each company with `serve:`, the logins of the scheduled extracts),
  prints the lines and exits. Why: the only way to check runbook logins once
  `auth status` leaves; fulfils S35. (S7, S35)
- X3. `briar init` writes both: `.briar.toml` as today, and `runbooks/<company>.yaml`,
  an example with a `serve:` block whose model and limits are commented out, so
  `serve` stops with "Field required" until someone chooses them (S5, S24). Why:
  `extract` reads `.briar.toml` (S34) and `serve` reads runbooks. (S7)
- X4. Code that only `auth login`, `auth status` and `runbook extract` use is removed,
  each piece proved unused with grep first (the terminal prompt, the write to
  `secrets.env`, the `--runbook` login lookup of `auth`, the one-shot runbook
  extract). The login reading, the login check and the reading of `secrets.env` stay.
  (S6, S7)
- X5. A 1.x user who types `briar auth ...` or `briar runbook ...` gets the Typer error
  `No such command`, as for the commands 2.0 already removed. The CHANGELOG and the
  README say what replaces them. (S6)
- X6. The site (usebriar.com `/docs`, private repo) changes after the 2.0 release, as a
  step of the release (S32), since it describes the 1.x on PyPI. (S32)

## Done without a question (follows from the decisions)

- Every hint that said `briar auth login ...` names the `BRIAR_*` variable and the
  `secrets.env` path briar reads instead (S7).
- `briar init --force` covers both files; without it, an existing file stops `init`
  as today. The runbook file is named after `--company` (`example` when none is
  given).
- The README lists the `BRIAR_*` names (S7); `agents/auth.md` and
  `agents/runbook.md` go, and the other manuals stop naming the two commands.
- `make smoke` drops `auth` and `runbook`.

## Open risks

- X5: a 1.x script that calls `briar runbook serve` fails with a generic error; only
  the CHANGELOG says what to use.
- X3: an example runbook with the limits commented out fails the load on purpose; a
  person who runs `serve` right after `init` sees "Field required" first.
- X6: until the release, the site keeps describing `auth` and `runbook`.
- X4: a removal can miss a caller that only a dynamic import reaches; the full test
  suite and `make smoke` are the guard.
