"""A CI that is already red on the base is not the fault of a ticket (WP3 of the production plan, decision P40).

When the CI of a PR fails, the agent is asked to fix it, up to `max_loops` times. A real run spent US$ 0.29 and 0.35 chasing a CI that was
red on the base branch before the ticket existed. Before the fix loop, the jobs that fail on the PR are compared, by name, with the jobs that
failed on the latest run of the base: when every one of them is red there too, the task stops with the reason and nothing is spent. A job that
fails only on the PR is still fixed. A read of the base that fails, or a failure with no job names, never stops a fix (it stays open)."""

from __future__ import annotations

import asyncio
from typing import List

from briar.extract._provider import CiFailure
from briar.serve.task import LimitHit, TaskContext


class BaseCi:
    """The comparison of the failed jobs of one PR with the base of its task."""

    def __init__(self, context: TaskContext) -> None:
        self._context = context

    async def stops(self, failures: List[CiFailure]) -> None:
        """Raise `LimitHit` when every failed job of the PR is red on the base too."""
        jobs = sorted({failure.job for failure in failures if failure.job})
        if not jobs:
            return
        context = self._context
        red = await asyncio.to_thread(context.sources.code_host().failing_jobs_on_base, context.item.repo, context.base)
        if set(jobs) <= set(red):
            raise LimitHit(
                f"CI fails on the base branch too ({', '.join(jobs)}): a ticket cannot fix a base that is already red, so no fix was tried. "
                f"Fix {context.base} first, then mark the PR ready if its own checks pass"
            )


__all__ = ["BaseCi"]
