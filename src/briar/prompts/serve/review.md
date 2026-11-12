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
