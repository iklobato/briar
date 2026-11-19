"""The gate in front of every push of `serve` (WP2 of the production plan, decision P33): no push adds a secret.

A hit gives the agent ONE fix loop. It is told the file and the line, never the value. A clean fix is made one commit with everything not pushed
yet, so the commit that held the value is not in the history of the origin either (a push sends every commit, and a secret that reached GitHub
cannot be taken back). A second hit stops the task with nothing pushed."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, List

from briar.extract._secrets import SecretGuard
from briar.serve.secrets import Hit, SecretScan
from briar.serve.task import AgentSteps, Budget, LimitHit, TaskContext


class SecretInDiff(LimitHit):
    """A push would add what looks like a credential, and one fix loop did not take it out."""


@dataclass(frozen=True)
class SecretGate:
    """The scan, the fix loop and the squash of one task, for the pushes of its flow."""

    context: TaskContext
    steps: AgentSteps
    budget: Budget
    verified: Callable[[str], Awaitable[None]]
    commit: Callable[[str], bool]

    async def clean(self) -> None:
        hits = await asyncio.to_thread(self.hits)
        if not hits:
            return
        self.budget.next_loop("a secret in the change")
        await self.steps.fix("the change adds what looks like a secret", self._details(hits))
        await self.verified("remove the secret")
        hits = await asyncio.to_thread(self.hits)
        if hits:
            raise SecretInDiff(self._left(hits))
        await asyncio.to_thread(self._squashed)

    def hits(self) -> List[Hit]:
        """What the next push would add that looks like a credential."""
        checkout, context = self.context.checkout, self.context
        onto = checkout.unpushed_base(context.worktree, context.branch, context.base)
        return SecretScan(SecretGuard.standard()).findings(checkout.changes(context.worktree, f"{onto}..HEAD"))

    def _details(self, hits: List[Hit]) -> str:
        where = "\n".join(f"- {hit.label()}" for hit in hits)
        return (
            f"These lines of your change look like a credential (the value is not shown here):\n{where}\n\n"
            "Take the value out of the files: read it from the environment or use a plain placeholder such as `<redacted>`. Never put a real secret in the repo."
        )

    def _left(self, hits: List[Hit]) -> str:
        where = ", ".join(hit.label() for hit in hits)
        return (
            f"the change adds what looks like a secret, and the fix did not remove it: {where}. The value is not shown and nothing was pushed. "
            "A secret finding needs a person: rotate it if it is real, or replace the placeholder"
        )

    def _squashed(self) -> None:
        """The commits not pushed yet become one: the tree is the same, the commit that held the value is gone."""
        checkout, context = self.context.checkout, self.context
        checkout.rewind(context.worktree, checkout.unpushed_base(context.worktree, context.branch, context.base))
        self.commit("work, with a secret taken out")


__all__ = ["SecretGate", "SecretInDiff"]
