Review this change for ticket {key} ({title}) as a careful senior reviewer would, before
a person sees it. Do not edit anything in this step.

The plan it was built from:

{plan}

The diff:

```diff
{diff}
```

Look for: a step of the plan that is missing, a bug, a missing or weak test, code that does
not match the conventions of the repo, and changes the ticket does not need. Answer
`approved: true` only when nothing needs a change; otherwise list each finding with the file
and what to change.

A change made so the verify commands pass (a test setting, a config file the tests need) is
part of the work, even when the ticket does not name it: the change is checked on a clean
copy of the commit, so without it the work cannot be verified. Reject it only when it hides a
failure (skipped or removed tests, lowered checks), and then say what to do instead. Reject
too a change that only adapts the repo to the machine it runs on: a plugin turned off in the
config, a path added to `sys.path` or `PYTHONPATH`, an environment variable set in a test.

Do not ask for a change of formatting (alignment, spacing, quotes, line breaks, import
order): the formatter of the repo runs before every commit and would undo it. Ask only
when the code is wrong, not when it looks different.

Earlier rounds of this review asked for:
{earlier}

The change was made for them. Do not ask for the opposite of an earlier ask; when an earlier
ask was wrong, say so and why in your finding.
