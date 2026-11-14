"""The steps both routines of `briar serve` share (spec 003 PR 2 and PR 3): the verify
loop (S20), the CI wait with fixes (S20), what briar writes on the ticket (S19, S26), the
stop on a limit (S23) and the record in the state (S17).

Every GitHub comment briar posts carries the hidden marker (V2)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import PurePosixPath
from functools import cached_property
from typing import Any, ClassVar, FrozenSet, List, Mapping, Tuple

from briar.errors import CliError
from briar.extract._provider import CiState, PullRequestDraft
from briar.serve.git import Shell
from briar.serve.state import WorkSource, WorkStatus
from briar.serve.task import AgentSteps, Budget, LimitHit, Plan, TaskContext
from briar.serve.work import Marker, WorkDay, WorkItem, WorkSources


class ScannerSilencing:
    """The files of a staged diff that silence a secret scanner: its ignore or baseline file,
    or an added line with its allow comment. A real run (2026-11-13): to pass a failed secret
    scan the agent wrote `.gitleaksignore` with the fingerprints of two real passwords."""

    FILES: ClassVar[FrozenSet[str]] = frozenset({".gitleaksignore", ".secrets.baseline", ".gitguardian.yaml", ".gitguardian.yml", ".trufflehogignore"})
    MARKS: ClassVar["re.Pattern[str]"] = re.compile(r"gitleaks:allow|pragma: allowlist secret|ggignore|trufflehog:ignore", re.IGNORECASE)
    NEW_FILE = "+++ b/"

    def __init__(self, diff: str) -> None:
        self._diff = diff

    def files(self) -> List[str]:
        found: List[str] = []
        current = ""
        for line in self._diff.splitlines():
            if line.startswith("+++ "):
                current = line[len(self.NEW_FILE) :] if line.startswith(self.NEW_FILE) else ""
                continue
            silences = PurePosixPath(current).name in self.FILES or (line.startswith("+") and bool(self.MARKS.search(line)))
            if current and silences and current not in found:
                found.append(current)
        return found


class TicketSide:
    """What briar writes on a ticket: comments (S19) and the status (S26)."""

    def __init__(self, sources: WorkSources) -> None:
        self._sources = sources

    def comment(self, item: WorkItem, text: str) -> None:
        if item.source is WorkSource.GITHUB:
            self._sources.code_host().comment(item.repo, int(item.number()), Marker().mark(text))
            return
        self._sources.jira().comment(item.key, text)

    def move(self, item: WorkItem, status: str) -> None:
        """Only Jira has a status to move; no name in the runbook: no move."""
        if status and item.source is WorkSource.JIRA:
            self._sources.jira().move(item.key, status)


class TaskFlow:
    """The shared steps of one task of a company."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    # Seconds between two looks at CI, and before "no check at all" means no CI.
    CI_POLL_SECONDS = 30
    CI_GRACE_SECONDS = 60
    SECONDS_PER_MINUTE = 60
    # The tail of a failed verify output the agent gets.
    OUTPUT_TAIL = 4000

    def __init__(self, context: TaskContext, steps: AgentSteps, budget: Budget) -> None:
        self._context = context
        self._steps = steps
        self._budget = budget

    async def verified(self, what: str) -> None:
        """Commit, run the verify commands, and fix until they pass (S20)."""
        await asyncio.to_thread(self.commit, what)
        failures = await asyncio.to_thread(self._verify_failures)
        if failures:
            await self._base_passes()
        while failures:
            self._budget.next_loop("the verify commands")
            await self._steps.fix("the verify commands fail", failures)
            await asyncio.to_thread(self.commit, "fix the verify commands")
            failures = await asyncio.to_thread(self._verify_failures)

    async def ci(self, number: int) -> None:
        """Wait for CI on the PR and have the agent fix a failed run (S20, S23)."""
        minutes = self._context.company.settings.ci_wait_minutes
        deadline = time.monotonic() + minutes * self.SECONDS_PER_MINUTE
        started = time.monotonic()
        while True:
            state = await asyncio.to_thread(self._context.sources.code_host().ci_state, self._context.item.repo, number)
            if state is CiState.PASSED or (state is CiState.NONE and time.monotonic() - started > self.CI_GRACE_SECONDS):
                return
            if state is CiState.FAILED:
                await self._fix_ci(number)
            if time.monotonic() > deadline:
                raise LimitHit(f"CI did not pass within ci_wait_minutes ({minutes})")
            await asyncio.sleep(self.CI_POLL_SECONDS)

    async def reviewed(self, plan: Plan, revisions: str) -> None:
        """The self-review of the change in `revisions` against `plan`, fixed until it
        approves (S20)."""
        asked: List[str] = []
        review = await self._steps.review(plan, await asyncio.to_thread(self._context.checkout.changes, self._context.worktree, revisions), asked)
        while not review.approved:
            listed = "\n".join(f"- {finding}" for finding in review.findings)
            try:
                self._budget.next_loop("the self-review")
            except LimitHit as stop:
                raise LimitHit(f"{stop}\n\nThe self-review still asks for:\n{listed}") from stop
            await self._steps.fix("the self-review found problems", listed)
            await self.verified("fix the self-review")
            # A real run flip-flopped (add a line, drop it, add it): each round sees the asks before it.
            asked.extend(review.findings)
            review = await self._steps.review(plan, await asyncio.to_thread(self._context.checkout.changes, self._context.worktree, revisions), asked)

    def spent(self) -> str:
        return self._budget.summary()

    async def push(self) -> None:
        await asyncio.to_thread(self._context.checkout.push, self._context.worktree, self._context.branch, self._context.token)

    async def blocked(self, report: str) -> None:
        """S23: push what there is, a draft PR with the report, the report on the ticket.
        The ticket is blocked even when posting fails (else the next cycle would redo it,
        and pay again): the failure joins the report in the state and the log."""
        head, _, detail = report.partition("\n\n")
        text = f"briar stopped: {head}. Spent {self._budget.summary()}." + (f"\n\n{detail}" if detail else "")
        known = self._context.company.state.book().entry(self._context.item.key)
        number = known.pr if known is not None else 0
        try:
            number = await asyncio.to_thread(self._draft, text)
        except CliError as failure:
            text = f"{text} The draft PR failed: {failure}."
        try:
            await asyncio.to_thread(TicketSide(self._context.sources).comment, self._context.item, text)
        except CliError as failure:
            text = f"{text} The ticket comment failed: {failure}."
        await self.record(WorkStatus.BLOCKED, {"pr": number, "report": text})

    async def record(self, status: WorkStatus, changes: Mapping[str, Any]) -> None:
        """Write the entry of the task with `status` and `changes`, keeping what the state
        already holds of it (the plan, the answered comments, the rounds)."""
        item = self._context.item
        known = self._context.company.state.book().entry(item.key)
        base = known or item.entry()
        day, before = self._spend_start
        spent = {**base.spent_by_day, day: before + self._budget.spent[0]}
        entry = base.model_copy(update={**changes, "status": status, "branch": self._context.branch, "spent_by_day": spent})
        await self._context.company.state.record(entry)
        link = f" {self._context.sources.style().pull_url(item.repo, entry.pr)}" if entry.pr else ""
        # A done task says what it cost too: before, only a blocked one did (real run, 2026-11-12).
        extra = {WorkStatus.BLOCKED: entry.report, WorkStatus.DONE: self.spent()}.get(status, "")
        self.LOG.info("serve: %s is %s%s %s", item.ref(), status.value, link, extra)

    @cached_property
    def _spend_start(self) -> Tuple[str, float]:
        """The day of this run and what the task spent that day before it, read at the first
        record of the run (the state holds nothing of this run yet). ponytail: a run past
        midnight UTC books all of its spend on the day it started."""
        day = WorkDay().today()
        known = self._context.company.state.book().entry(self._context.item.key)
        return day, (known.spent_by_day.get(day, 0.0) if known is not None else 0.0)

    def changed(self) -> bool:
        """The branch holds a change of its own: no PR is ever opened for the base alone."""
        return bool(self._context.checkout.diff(self._context.worktree, self._context.base).strip())

    def commit(self, what: str) -> bool:
        self._format()
        self._refuse_silenced_scanners()
        return self._context.checkout.commit_all(self._context.worktree, f"{self._context.item.ref()}: {what}", self._context.identity)

    def _format(self) -> None:
        """Decision R11: run the format commands of the repo in the worktree. A failing one is
        logged and the task goes on: the verify commands are what decides."""
        item, shell = self._context.item, Shell()
        for command in self._context.company.settings.format_for(item.repo):
            done = shell.line(command, self._context.worktree)
            if not done.ok():
                self.LOG.info("serve: %s format `%s` failed (exit %s): %s", item.ref(), command, done.code, done.output.strip()[-self.OUTPUT_TAIL :])

    def _refuse_silenced_scanners(self) -> None:
        """Nothing that silences a secret scanner is committed: briar drops it and stops, since
        a secret finding needs a person (rotate it, or mark a false positive by hand)."""
        checkout, worktree = self._context.checkout, self._context.worktree
        silenced = ScannerSilencing(checkout.staged_diff(worktree)).files()
        if silenced:
            checkout.drop(worktree, silenced)
            raise LimitHit(f"the change silences a secret scanner ({', '.join(silenced)}); briar dropped that part. A secret finding needs a person: rotate the secret, or mark a false positive by hand")

    async def _fix_ci(self, number: int) -> None:
        self._budget.next_loop("CI")
        failures = await asyncio.to_thread(self._context.sources.code_host().list_ci_failures, self._context.item.repo, number)
        details = "\n\n".join(f"{failure.job} / {failure.step} ({failure.url}):\n{failure.log_tail}" for failure in failures)
        style = self._context.sources.style()
        await self._steps.fix(f"CI fails on the {style.noun()} #{number} ({style.pull_url(self._context.item.repo, number)})", details or "(no failure log was found)")
        await self.verified("fix CI")
        await self.push()

    def _draft(self, text: str) -> int:
        """The PR of the task as a draft with the report: the open one, or a new one."""
        known = self._context.company.state.book().entry(self._context.item.key)
        self.commit("work so far")
        if not (known and known.pr) and not self.changed():
            return 0
        self._context.checkout.push(self._context.worktree, self._context.branch, self._context.token)
        github = self._context.sources.code_host()
        if known is not None and known.pr:
            github.make_draft(self._context.item.repo, known.pr)
            github.comment(self._context.item.repo, known.pr, Marker().mark(text))
            return known.pr
        draft = PullRequestDraft(self._context.branch, self._context.base, self._context.title, text, draft=True)
        return int(github.open_pull(self._context.item.repo, draft))

    async def _base_passes(self) -> None:
        """A verify that fails on the base too is no failure of the change (a real run: a
        pytest plugin of the machine); fixing it would put a workaround of this machine in the
        PR, so the task stops with the report instead."""
        base = f"origin/{self._context.base}"
        failures = await asyncio.to_thread(self._failures_at, base)
        if failures:
            raise LimitHit(f"the verify commands fail on {base} too, before any change: fix the environment or the `verify` commands of the runbook.{self._path_hint(failures)}\n\n{failures}")

    def _path_hint(self, failures: str) -> str:
        """Real run (2026-11-14): under launchd `uv` was not found; say what to fix."""
        if "(exit 127)" not in failures:
            return ""
        return " A program the verify commands run is not on the PATH of this process: launchd and systemd do not load your shell profile, so put its folder in the PATH of the service."

    def _verify_failures(self) -> str:
        """The verify commands, run on a clean copy of the commit: what will be pushed."""
        failures = self._failures_at("HEAD")
        return f"{failures}{self._ignored_note()}" if failures else ""

    def _failures_at(self, revision: str) -> str:
        checkout, worktree, shell = self._context.checkout, self._context.worktree, Shell()
        copy = checkout.verify_copy(worktree, revision)
        found = [(command, shell.line(command, copy)) for command in self._context.company.settings.verify_for(self._context.item.repo)]
        checkout.drop_copy(worktree, copy)
        results = ", ".join(f"`{command}` {'ok' if done.ok() else f'exit {done.code}'}" for command, done in found) or "no verify commands set"
        self.LOG.info("serve: %s verify on %s: %s", self._context.item.ref(), revision, results)
        return "\n\n".join(f"`{command}` failed (exit {done.code}):\n{done.output[-self.OUTPUT_TAIL :]}" for command, done in found if not done.ok())

    def _ignored_note(self) -> str:
        ignored = self._context.checkout.ignored(self._context.worktree)
        return f"\n\ngit ignores these files of your folder, so they are not in the commit: {', '.join(ignored)}" if ignored else ""


__all__ = ["TaskFlow", "TicketSide"]
