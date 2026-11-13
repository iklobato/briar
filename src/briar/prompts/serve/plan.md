Write the plan for this ticket. Read the code and the knowledge you need first; do not
edit anything in this step.

Ticket {key}: {title}

{description}

Answer with the plan: a short summary of the change, the steps in order (each one small
enough to check), the tests that prove it, and the risks a reviewer should look at. If the
ticket has no clear acceptance criteria, write the ones you will build to in the summary.

Set `needs_code` to false only when the ticket needs no change in this repo at all (an
operations task such as copying data or running a job, or work that is already done and
tested); then nothing is posted and no branch is made. Otherwise set it to true.
