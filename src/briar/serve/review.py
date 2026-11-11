"""The review routine of `briar serve` (spec 003 PR 3; decisions V6 to V11 in
`docs/decisions/2026-11-11-briar-2-review-routine.md`):

count the round -> merge the base when the PR is in conflict -> one agent call over the
new comments -> verify loop -> self-review loop -> push -> a reply in each inline thread
and one quoting comment for the rest -> wait for CI, fixing a failure -> record the
answered comments. A round that changes nothing only replies."""

from __future__ import annotations

import asyncio
from typing import Dict, List, Sequence

from briar.errors import CliError
from briar.extract._provider import CommentKind, ReviewComment, ThreadReply
from briar.serve.flow import TaskFlow
from briar.serve.state import WorkStatus
from briar.serve.task import AgentSteps, Answers, LimitHit, Plan, TaskContext
from briar.serve.work import Marker


class ReviewReplies:
    """V9: an inline comment gets a reply in its thread; the Conversation comments, the
    review bodies and the note of a base merge go in one comment. Each carries the marker."""

    # The lines of a comment quoted in the one comment.
    QUOTE_LINES = 6

    def __init__(self, context: TaskContext, answers: Answers, commit: str) -> None:
        self._context = context
        self._answers = answers
        self._commit = commit

    def post(self, merged: Sequence[str]) -> None:
        item = self._context.item
        github = self._context.sources.github()
        for comment in [comment for comment in item.comments if comment.kind is CommentKind.INLINE]:
            github.reply_in_thread(item.repo, ThreadReply(item.pr, comment.thread, Marker().mark(self._text(comment))))
        parts = [*(self._quoted(comment) for comment in item.comments if comment.kind is not CommentKind.INLINE), *self._merge_note(merged)]
        if parts:
            github.comment(item.repo, item.pr, Marker().mark("\n\n---\n\n".join(parts)))

    def _text(self, comment: ReviewComment) -> str:
        answer = self._answers.of(comment)
        return f"{answer.reply}\n\nChanged in {self._commit}." if answer.changed and self._commit else answer.reply

    def _quoted(self, comment: ReviewComment) -> str:
        quote = "\n".join(f"> {line}" for line in comment.body.splitlines()[: self.QUOTE_LINES])
        return f"{comment.author} wrote:\n{quote}\n\n{self._text(comment)}"

    def _merge_note(self, merged: Sequence[str]) -> List[str]:
        if not self._context.item.conflict:
            return []
        files = f", resolving the conflicts in {', '.join(merged)}" if merged else ""
        return [f"briar merged origin/{self._context.base} into this branch{files} ({self._commit})."]


class ReviewRun:
    """One review round on a briar PR."""

    # The short commit named in the replies.
    SHORT_SHA = 7

    def __init__(self, context: TaskContext, steps: AgentSteps, flow: TaskFlow) -> None:
        self._context = context
        self._steps = steps
        self._flow = flow

    async def work(self) -> None:
        start = await self._start()
        item = self._context.item
        merged = await self._merged() if item.conflict else []
        answers = await self._steps.answer(item.comments) if item.comments else Answers({})
        await asyncio.to_thread(self._flow.commit, "answer the review")
        head = await asyncio.to_thread(self._context.checkout.head, self._context.worktree)
        if head != start:
            await self._checked(start)
            head = await asyncio.to_thread(self._context.checkout.head, self._context.worktree)
        commit = head[: self.SHORT_SHA] if head != start else ""
        await asyncio.to_thread(ReviewReplies(self._context, answers, commit).post, merged)
        if head != start:
            await self._flow.ci(item.pr)
        await self._flow.record(WorkStatus.DONE, {"answered": self._answered()})

    async def _start(self) -> str:
        """V11: count the round, or stop when the PR had `max_review_rounds` already."""
        known = self._context.company.state.book().entry(self._context.item.key)
        rounds = known.rounds if known is not None else 0
        limit = self._context.company.settings.max_review_rounds
        if rounds >= limit:
            raise LimitHit(f"max_review_rounds ({limit}) reached on PR #{self._context.item.pr}")
        await self._flow.record(WorkStatus.PR_OPEN, {"rounds": rounds + 1})
        return await asyncio.to_thread(self._context.checkout.head, self._context.worktree)

    async def _merged(self) -> List[str]:
        """V6: merge the base; the agent resolves what conflicts. A merge that cannot be
        finished is dropped before the stop, so the draft of S23 never holds a marker."""
        checkout, worktree = self._context.checkout, self._context.worktree
        conflicted = await asyncio.to_thread(checkout.merge, worktree, self._context.base, self._context.identity)
        if not conflicted:
            return []
        try:
            await self._resolved(conflicted)
        except CliError:
            await asyncio.to_thread(checkout.abort_merge, worktree)
            raise
        return conflicted

    async def _resolved(self, conflicted: Sequence[str]) -> None:
        base = self._context.base
        files = "\n".join(f"- {name}" for name in conflicted)
        details = f"Resolve the conflicts in:\n{files}\n\nKeep what both sides need, and remove every <<<<<<<, ======= and >>>>>>> line."
        await self._steps.fix(f"merging origin/{base} into the branch left conflicts", details)
        left = await asyncio.to_thread(self._context.checkout.marked, self._context.worktree, conflicted)
        if left:
            raise LimitHit(f"conflict markers left in {', '.join(left)} after merging origin/{base}")
        await asyncio.to_thread(self._context.checkout.finish_merge, self._context.worktree, self._context.identity)

    async def _checked(self, start: str) -> None:
        """S20, V8: the verify loop and the self-review of the round, then the push."""
        await self._flow.verified("fix the verify commands")
        await self._flow.reviewed(self._plan(), f"{start}..HEAD")
        await self._flow.push()

    def _plan(self) -> Plan:
        """What the round had to do, for the self-review."""
        item = self._context.item
        asks = [f"answer {comment.author}: {comment.body.splitlines()[0] if comment.body else ''}" for comment in item.comments]
        merge = [f"merge origin/{self._context.base} and resolve its conflicts"] if item.conflict else []
        return Plan(f"Answer the review comments on PR #{item.pr}", tuple(merge + asks))

    def _answered(self) -> Dict[str, str]:
        known = self._context.company.state.book().entry(self._context.item.key)
        answered = dict(known.answered) if known is not None else {}
        return {**answered, **{comment.key(): comment.updated_at for comment in self._context.item.comments}}


__all__ = ["ReviewReplies", "ReviewRun"]
