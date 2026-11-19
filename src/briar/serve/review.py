"""The review routine of `briar serve` (spec 003 PR 3; decisions V6 to V11 in
`docs/decisions/2026-11-11-briar-2-review-routine.md`):

count the round -> merge the base when the PR is in conflict -> one agent call over the
new comments -> verify loop -> self-review loop -> push -> a reply in each inline thread
and one quoting comment for the rest -> wait for CI, fixing a failure -> record the
answered comments. A round that changes nothing only replies."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Mapping, Optional, Sequence

from briar.errors import CliError
from briar.extract._provider import CommentKind, ReviewComment, ThreadReply
from briar.serve.pause import ModelPause
from briar.serve.flow import TaskFlow
from briar.serve.state import MoneyStop, WorkEntry, WorkStatus
from briar.serve.task import AgentSteps, Answers, BudgetExhausted, LimitHit, Plan, TaskContext
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
        github = self._context.sources.code_host()
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
        # The replies of the round are posted, so a stop for money does not give the round back (B7, B18).
        self._replied = False
        # Briar made this PR a draft when the ticket ran out of money, so briar marks it ready again (B6).
        self._drafted = False
        # The CI has not passed for this PR yet (a day pause in its wait, or a draft of briar), so even a round that changes
        # no code waits for it before it clears the marker or marks the PR ready (audit 3 and 4).
        self._owes_ci = False
        # The status the PR had before the round counted itself, which a pause of the day cap puts back (B13).
        self._previous: Optional[WorkStatus] = None

    async def work(self) -> None:
        resumed = self._context.item.ci_only
        start = await (self._resume() if resumed else self._start())
        # A resumed wait comes after the replies: a stop gives no round back, because none was counted.
        self._replied = resumed
        try:
            await (self._waited() if resumed else self._round(start))
        except BudgetExhausted as stop:
            await self._out_of_money(stop)
        except ModelPause:
            await self._paused()
            raise

    async def _waited(self) -> None:
        """Decision B13: the CI wait and the CI fix of a round the day cap paused after its replies. No reply is posted again
        and nobody is asked again."""
        await self._flow.ci(self._context.item.pr)
        await self._done({})

    async def _paused(self) -> None:
        """Decision B13, and WP1: a pause (the day cap, a refused login, the plan limit) pauses the round (the routine writes
        the note and tells `serve` what the pause asks of it). What was never verified goes back to
        what the origin has. Before the replies the round is given back and the status comes back; after them, only the CI
        wait is left, so the entry says so and the finder emits it again when the day has room."""
        await self._back_to_the_origin()
        if self._replied:
            await self._flow.record(WorkStatus.PR_OPEN, {"ci_paused": True})
            return
        await self._flow.record(self._previous or WorkStatus.PR_OPEN, {"rounds": self._rounds_kept()})

    async def _back_to_the_origin(self) -> None:
        """Decisions B6, B18: a stop drops every local commit and edit and goes back to the pushed head, which the origin ref
        follows after each push (the CI fix pushes too, so a head kept at the start of the round went stale: audit 12)."""
        context = self._context
        pushed = await asyncio.to_thread(context.checkout.pushed, context.worktree, context.branch)
        await asyncio.to_thread(context.checkout.discard, context.worktree, pushed)

    async def _round(self, start: str) -> None:
        item = self._context.item
        merged = await self._merged() if item.conflict else []
        answers = await self._steps.answer(item.comments) if item.comments else Answers({})
        await asyncio.to_thread(self._flow.commit, "answer the review")
        head = await self._head()
        if head != start:
            await self._checked(start)
            head = await self._head()
        commit = head[: self.SHORT_SHA] if head != start else ""
        await asyncio.to_thread(ReviewReplies(self._context, answers, commit).post, merged)
        self._replied = True
        await self._finished(start, head)

    async def _finished(self, start: str, head: str) -> None:
        saved = {"answered": self._answered()}
        if head == start and not self._owes_ci:
            await self._done(saved)
            return
        # Saved before the CI wait: a stop there must not answer the same comments again.
        await self._flow.record(WorkStatus.PR_OPEN, saved)
        await self._flow.ci(self._context.item.pr)
        await self._done({})

    async def _done(self, changes: Mapping[str, Any]) -> None:
        """The round is over. A PR briar made a draft for a stop for money is marked ready first (B6)."""
        item = self._context.item
        if self._drafted:
            await asyncio.to_thread(self._context.sources.code_host().make_ready, item.repo, item.pr)
        await self._flow.record(WorkStatus.DONE, {**changes, "money_stop": None, "ci_paused": False})

    async def _out_of_money(self, stop: BudgetExhausted) -> None:
        """Decisions B6, B7, B18: what was never verified goes (back to the start of the round, or
        to what was pushed), the PR becomes a draft with a comment that says why, and the ticket
        is blocked for money. A round with no reply is given back."""
        context = self._context
        host, item = context.sources.code_host(), context.item
        await self._back_to_the_origin()
        already = (await asyncio.to_thread(host.pull_state, item.repo, item.pr)).draft
        if not already:
            await asyncio.to_thread(host.make_draft, item.repo, item.pr)
        # A draft briar made at an earlier stop stays briar own until a round ends (a second stop after a small raise).
        drafted = self._drafted or not already
        text = f"briar stopped: {stop}. Spent {self._flow.spent()}. A person continues, or raises max_usd in the runbook and restarts `briar serve`."
        await asyncio.to_thread(host.comment, item.repo, item.pr, Marker().mark(text))
        money = MoneyStop(cap=context.company.settings.max_usd, resume_to=WorkStatus.PR_OPEN.value, drafted=drafted)
        await self._flow.record(WorkStatus.BLOCKED, {"report": text, "rounds": self._rounds_kept(), "money_stop": money})

    def _rounds_kept(self) -> int:
        known = self._context.company.state.book().entry(self._context.item.key)
        counted = known.rounds if known is not None else 0
        return counted if self._replied else max(counted - 1, 0)

    async def _head(self) -> str:
        return await asyncio.to_thread(self._context.checkout.head, self._context.worktree)

    async def _start(self) -> str:
        """V11: count the round, or stop when the PR had `max_review_rounds` already. It also
        reads whether briar made this PR a draft for a stop for money (B6)."""
        known = self._context.company.state.book().entry(self._context.item.key)
        rounds = known.rounds if known is not None else 0
        limit = self._context.company.settings.max_review_rounds
        if rounds >= limit:
            raise LimitHit(f"max_review_rounds ({limit}) reached on PR #{self._context.item.pr}")
        self._drafted = self._made_a_draft(known)
        self._owes_ci = self._owed(known)
        self._previous = known.status if known is not None else WorkStatus.PR_OPEN
        await self._flow.record(WorkStatus.PR_OPEN, {"rounds": rounds + 1})
        return await self._head()

    async def _resume(self) -> str:
        """Decision B13: the head of a paused CI wait. No round is counted."""
        known = self._context.company.state.book().entry(self._context.item.key)
        self._drafted = self._made_a_draft(known)
        self._owes_ci = self._owed(known)
        return await self._head()

    def _made_a_draft(self, known: Optional[WorkEntry]) -> bool:
        return known is not None and known.money_stop is not None and known.money_stop.drafted

    def _owed(self, known: Optional[WorkEntry]) -> bool:
        """A day pause in the CI wait left the CI fix owed, and a draft of briar was never marked ready on a green CI."""
        return (known is not None and known.ci_paused) or self._drafted

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
