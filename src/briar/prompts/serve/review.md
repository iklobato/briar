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
