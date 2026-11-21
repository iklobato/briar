"""The parts of one task of `briar serve` (spec 003 PR 2 and PR 3): where it works
(`TaskContext`, made by `TaskPrep`), what it may spend (`Budget`, S23), the plan, the
answers to review comments (V7), and the agent steps (plan, build, fix, self-review,
answer; S18 to S20)."""

from __future__ import annotations

import asyncio
import importlib.resources
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, ClassVar, List, Mapping, Sequence, Tuple

from briar.errors import CliError
from briar.extract._provider import ReviewComment
from briar.serve.agent import Access, AgentAnswer, AgentRequest, AgentRunner
from briar.serve.day import DUST, DayPause, Grant
from briar.serve.git import GitIdentity, RepoCheckout, Shell
from briar.serve.page import TaskFacts
from briar.serve.pause import Refused
from briar.serve.rules import TaskRules
from briar.serve.state import WorkSource
from briar.serve.work import BranchName, ServeCompany, WorkDay, WorkItem, WorkSources


class LimitHit(CliError):
    """A limit of the task hit (S23); the message is the report."""


class BudgetExhausted(LimitHit):
    """The ticket spent its `max_usd` (decision B2): the one limit a person can lift, by raising it."""


@dataclass(frozen=True)
class Budget:
    """The money and the fix loops one task has left (S23). `spent` starts at what the earlier
    runs of the ticket spent (decision B2); `loops` starts at zero in every run (B4)."""

    max_usd: float
    max_loops: int
    spent: List[float] = field(default_factory=lambda: [0.0])
    loops: List[int] = field(default_factory=lambda: [0])

    def left_usd(self) -> float:
        return self.max_usd - self.spent[0]

    def require(self) -> None:
        """No call with nothing left: the SDK would get a `max_budget_usd` of zero (or of a hair above it: `DUST`)."""
        if self.left_usd() <= DUST:
            raise BudgetExhausted(self._report())

    def add(self, cost: float) -> None:
        self.spent[0] += cost

    def check(self, answer: AgentAnswer) -> None:
        """The SDK stop `error_max_budget_usd` is the ticket cap here; `AgentSteps._ask` tells the day cap first (decision B13)."""
        if answer.reason == "error_max_budget_usd" or self.left_usd() <= DUST:
            raise BudgetExhausted(self._report())

    def spend(self, answer: AgentAnswer) -> None:
        self.add(answer.cost_usd)
        self.check(answer)

    def _report(self) -> str:
        return f"the task spent its max_usd of {self.max_usd}"

    def next_loop(self, why: str) -> None:
        self.loops[0] += 1
        if self.loops[0] > self.max_loops:
            raise LimitHit(f"max_loops ({self.max_loops}) reached while fixing: {why}")

    def summary(self) -> str:
        return f"{self.money()}, {self.loops[0]} of {self.max_loops} fix loops"

    def money(self) -> str:
        """What the ticket spent, all its runs included, against its cap. The fix loops are of this run only, so they stay out."""
        return f"US$ {self.spent[0]:.2f} of {self.max_usd}"


@dataclass(frozen=True)
class TaskContext:
    """Where one ticket task works and what it knows."""

    item: WorkItem
    company: ServeCompany
    worktree: Path
    checkout: RepoCheckout
    base: str
    branch: str
    title: str
    description: str
    identity: GitIdentity
    token: str
    sources: WorkSources
    # The observer the code calls when it knows something new about the task (decisions W3 and W4):
    # the page of the task is the memory of the task.
    facts: "TaskFacts"


class ServeCache:
    """The folder of the clones and worktrees: `$XDG_CACHE_HOME/briar`, else `~/.cache/briar`."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    def root(self) -> Path:
        base = self._environ.get("XDG_CACHE_HOME", "").strip()
        return (Path(base) if base else Path.home() / ".cache") / "briar"


@dataclass(frozen=True)
class TaskRefs:
    """The branch of a task, the base of its PR, and where a new worktree starts: the base
    for a ticket, the branch of the PR for a review round."""

    branch: str
    base: str
    start: str


class TaskPrep:
    """Makes the context of a task: the clone, the worktree, the branch and the title
    (S27), the ticket text, and who the commits are from (S2)."""

    def __init__(self, sources: WorkSources, shell: Shell, cache: Path) -> None:
        self._sources = sources
        self._shell = shell
        self._cache = cache

    def sources(self) -> WorkSources:
        """The code host and trackers the tasks of this prep work with."""
        return self._sources

    def context(self, company: ServeCompany, item: WorkItem) -> TaskContext:
        """A ticket: a new branch from the base."""
        branch = BranchName(company.settings).of(item)
        base = self._sources.code_host().default_branch(item.repo)
        return self._made(company, item, TaskRefs(branch, base, base))

    def review_context(self, company: ServeCompany, item: WorkItem) -> TaskContext:
        """A review round: the branch of the PR, brought up to `origin` (V6, V7)."""
        known = company.state.book().entry(item.key)
        if known is None or not known.branch:
            raise CliError(f"serve: no branch recorded for {item.key}; cannot work on PR #{item.pr}")
        base = self._sources.code_host().default_branch(item.repo)
        context = self._made(company, item, TaskRefs(known.branch, base, known.branch))
        context.checkout.sync(context.worktree, known.branch)
        return context

    def _made(self, company: ServeCompany, item: WorkItem, refs: TaskRefs) -> TaskContext:
        style = self._sources.style()
        token = self._sources.code_host().resolve_token()
        checkout = RepoCheckout(self._shell, self._cache, style.remote(item.repo))
        checkout.update(token)
        worktree = checkout.worktree(refs.branch, refs.start, self._cache / "worktrees" / company.name / refs.branch.replace("/", "__"))
        title = company.settings.title.format_map({"key": item.ref(), "title": item.title, "number": item.number()})
        identity = style.identity()
        facts = TaskFacts(company, item)
        return TaskContext(item, company, worktree, checkout, refs.base, refs.branch, title, self._describe(item), identity, token, self._sources, facts)

    def _describe(self, item: WorkItem) -> str:
        """The ticket text; "" when the tracker cannot give it (its `get_ticket` logs why)."""
        tracker = self._sources.issues() if item.source is WorkSource.GITHUB else self._sources.jira()
        ticket = tracker.get_ticket(item.repo, item.key)
        return ticket.description if ticket is not None else ""


@dataclass(frozen=True)
class Plan:
    """The plan of the agent (S19), from its structured answer."""

    summary: str
    steps: Tuple[str, ...] = ()
    tests: Tuple[str, ...] = ()
    risks: Tuple[str, ...] = ()
    # The topics of the knowledge the agent read before it planned, so a person sees what it knew.
    sources: Tuple[str, ...] = ()
    # False for a ticket with nothing to change in the repo (an operations ticket, decision D1).
    needs_code: bool = True

    SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "string"}},
            "tests": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "sources": {"type": "array", "items": {"type": "string"}},
            "needs_code": {"type": "boolean"},
        },
        "required": ["summary", "steps", "needs_code"],
    }

    @classmethod
    def from_answer(cls, answer: AgentAnswer) -> "Plan":
        found = answer.structured
        if not found:
            return cls(answer.text.strip() or "(the agent gave no plan)")
        steps, tests, risks, sources = (tuple(str(entry) for entry in found.get(name, [])) for name in ("steps", "tests", "risks", "sources"))
        return cls(str(found.get("summary", "")), steps=steps, tests=tests, risks=risks, sources=sources, needs_code=bool(found.get("needs_code", True)))

    def markdown(self) -> str:
        parts = [
            self.summary,
            self._list("Steps", self.steps),
            self._list("Tests", self.tests),
            self._list("Risks", self.risks),
            self._list("Sources read", self.sources),
        ]
        return "\n\n".join(part for part in parts if part)

    def _list(self, heading: str, entries: Tuple[str, ...]) -> str:
        return f"{heading}:\n" + "\n".join(f"{index}. {entry}" for index, entry in enumerate(entries, 1)) if entries else ""


@dataclass(frozen=True)
class Review:
    """The answer of the self-review (S20)."""

    approved: bool
    findings: Tuple[str, ...] = ()

    SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {"approved": {"type": "boolean"}, "findings": {"type": "array", "items": {"type": "string"}}},
        "required": ["approved"],
    }

    @classmethod
    def from_answer(cls, answer: AgentAnswer) -> "Review":
        found = answer.structured
        return cls(bool(found.get("approved", False)), tuple(str(entry) for entry in found.get("findings", [])))


@dataclass(frozen=True)
class CommentAnswer:
    """What the agent says about one review comment (V7, V9)."""

    changed: bool
    reply: str


@dataclass(frozen=True)
class Notes:
    """What the agent writes in the page of the task at the end of a workflow (decision W4): the
    Notes, one text. The code writes the Facts; the Notes are the agent's."""

    SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {"notes": {"type": "string"}},
        "required": ["notes"],
    }


@dataclass(frozen=True)
class Answers:
    """The answer of the agent for every comment of a round, by `ReviewComment.key()`; a
    comment it skipped still gets a reply (S22: every comment gets one)."""

    by_key: Mapping[str, CommentAnswer]

    SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "changed": {"type": "boolean"}, "reply": {"type": "string"}},
                    "required": ["id", "changed", "reply"],
                },
            }
        },
        "required": ["answers"],
    }
    MISSING: ClassVar[CommentAnswer] = CommentAnswer(False, "briar gave no answer for this comment; a person needs to look at it.")

    @classmethod
    def from_answer(cls, answer: AgentAnswer) -> "Answers":
        rows = [row for row in answer.structured.get("answers", []) if isinstance(row, dict)]
        return cls({str(row.get("id", "")): CommentAnswer(bool(row.get("changed")), str(row.get("reply", "")).strip()) for row in rows})

    def of(self, comment: ReviewComment) -> CommentAnswer:
        found = self.by_key.get(comment.key())
        return found if found is not None and found.reply else self.MISSING


class Prompts:
    """The prompt files of `briar.prompts` (`serve/*.md`), filled in."""

    def text(self, name: str, values: Mapping[str, str]) -> str:
        template = importlib.resources.files("briar.prompts").joinpath("serve").joinpath(f"{name}.md").read_text()
        return template.format_map(values)


class Fenced:
    """Text written by people (a ticket) between fences, so it cannot pass for the rest of a prompt (decision P36 of the production
    plan). A run of the fence inside the text is broken with spaces, so the text cannot close its own fence."""

    FENCE: ClassVar[str] = "~~~"
    RUN: ClassVar["re.Pattern[str]"] = re.compile(r"~{3,}")

    def text(self, label: str, body: str) -> str:
        safe = self.RUN.sub(lambda found: " ".join(found.group()), body)
        return f"{self.FENCE}{label}\n{safe}\n{self.FENCE}"


class AgentSteps:
    """The agent calls of one task, each one paid from the budget."""

    # A diff longer than this is cut for the self-review prompt.
    DIFF_LIMIT = 60_000

    def __init__(self, agent: AgentRunner, context: TaskContext, budget: Budget) -> None:
        self._agent = agent
        self._context = context
        self._budget = budget

    async def plan(self) -> Plan:
        title, description = self._context.item.title, self._context.description or "(no description)"
        prompt = Prompts().text("plan", {"key": self._context.item.ref(), "ticket": Fenced().text("ticket", f"{title}\n\n{description}")})
        return Plan.from_answer(await self._ask(prompt, Plan.SCHEMA, Access.READ))

    async def build(self, plan: Plan) -> None:
        await self._ask(Prompts().text("build", {**self._fields(), "plan": plan.markdown(), "verify": self._verify()}), {}, Access.EDIT)

    async def fix(self, what: str, details: str) -> None:
        await self._ask(Prompts().text("fix", {**self._fields(), "what": what, "details": details}), {}, Access.EDIT)

    async def answer(self, comments: Tuple[ReviewComment, ...]) -> Answers:
        """V7: one call over every new comment of the round; it may edit."""
        listed = "\n\n".join(self._listed(comment) for comment in comments)
        prompt = Prompts().text("comments", {**self._fields(), "comments": listed, "verify": self._verify()})
        return Answers.from_answer(await self._ask(prompt, Answers.SCHEMA, Access.EDIT))

    async def notes(self, facts: str) -> str:
        """The Notes of the task (decisions W3 and W4): the agent writes what it thinks and what is
        left, at the end of a workflow. One call, no tools; it is paid from the ticket cap."""
        prompt = Prompts().text("notes", {**self._fields(), "facts": facts})
        answer = await self._ask(prompt, Notes.SCHEMA, Access.ANSWER)
        return str(answer.structured.get("notes", "")).strip()

    async def notes_of(self, facts: "TaskFacts", key: str) -> None:
        """The Notes of the task, at the end of a workflow (decision W4). It is outside the blocks of
        the routines on purpose: a paid call that is only a note must never turn a ready PR back into
        a draft. A call the money cap stops is a Fact, not a stop."""
        from briar.serve.page import TaskNotes

        await TaskNotes(self, facts).write(key)

    async def review(self, plan: Plan, diff: str, asked: Sequence[str]) -> Review:
        earlier = "\n".join(f"- {finding}" for finding in asked) or "- (none: this is the first round)"
        values = {**self._fields(), "plan": plan.markdown(), "diff": diff[: self.DIFF_LIMIT], "earlier": earlier, "rules": self._rules().review_block()}
        prompt = Prompts().text("review", values)
        return Review.from_answer(await self._ask(prompt, Review.SCHEMA, Access.READ))

    def _listed(self, comment: ReviewComment) -> str:
        where = f" on {comment.file_path}:{comment.line}" if comment.file_path else ""
        quoted = "\n".join(f"> {line}" for line in comment.body.splitlines())
        return f"id `{comment.key()}`, {comment.kind.value} comment by {comment.author}{where}:\n{quoted}"

    def _fields(self) -> Mapping[str, str]:
        return {"key": self._context.item.ref(), "title": self._context.item.title}

    def _verify(self) -> str:
        return "\n".join(f"- `{command}`" for command in self._context.company.settings.verify_for(self._context.item.repo)) or "- (no verify commands set)"

    def _rules(self) -> TaskRules:
        """The rule sets the runbook turns on for the repo of this task (decision Q4): in the system
        prompt of every call, and the checklist in the self review."""
        return self._context.company.rules.for_task(self._context.item.repo, self._context.worktree)

    def _known(self, path: Path) -> str:
        """A real run sent the agent to read files that did not exist yet."""
        return str(path) if path.exists() else "(none yet)"

    async def _booked(self, answer: AgentAnswer) -> None:
        """Decision B5: what a call cost is saved before any limit can stop the task, so a kill
        or a restart finds it."""
        item = self._context.item
        await self._context.company.state.add_spend(item.entry(), WorkDay().today(), answer.cost_usd)

    async def _ask(self, prompt: str, schema: Mapping[str, Any], access: Access) -> AgentAnswer:
        self._budget.require()
        ticket_left = self._budget.left_usd()
        request = AgentRequest(prompt, Prompts().text("system", self._values()), self._context.worktree, ticket_left, schema, access)
        grant = await self._context.company.grant(ticket_left)
        try:
            answer = await self._answered(replace(request, max_usd=grant.amount))
        finally:
            # After the booking, so the booked spend plus what is in flight never under counts (decision B12).
            self._context.company.day.release(grant)
        self._budget.add(answer.cost_usd)
        self._refused(answer)
        self._day_stop(answer, grant, ticket_left)
        self._budget.check(answer)
        if answer.failed:
            raise LimitHit(f"the agent stopped: {answer.reason or 'no reason given'}")
        self._context.company.gate.clear()
        return answer

    def _refused(self, answer: AgentAnswer) -> None:
        """WP1: the model refused the call, its login or the plan limit. The cost is booked already; the task pauses and is not
        blocked (nothing is written on its ticket), and `ServeRoutine` tells `serve` what the pause asks of it."""
        pause = Refused(self._context.company.name).pause(answer)
        if pause is not None:
            raise pause

    async def _answered(self, request: AgentRequest) -> AgentAnswer:
        answer = await self._timed(request)
        await self._booked(answer)
        return answer

    async def _timed(self, request: AgentRequest) -> AgentAnswer:
        """Audit 9: a call that never answers must not hold its share of the day for ever. It is cut after `call_minutes` and the
        task is blocked with that report. What the cut call cost is not known, so it is not booked (the grant bounds it). The
        timeout is caught by its asyncio name: on Python 3.10 it is not the builtin `TimeoutError`, and would escape `EXPECTED`."""
        settings = self._context.company.settings
        try:
            return await asyncio.wait_for(self._agent.ask(request), settings.call_seconds())
        except asyncio.TimeoutError as slow:
            raise LimitHit(f"the agent did not answer within call_minutes ({settings.call_minutes})") from slow

    def _day_stop(self, answer: AgentAnswer, grant: Grant, ticket_left: float) -> None:
        """Decision B13: the SDK stopped the call at its grant and the DAY was the smaller limit, so the task pauses
        (it is not blocked: a costly day is not the fault of the ticket). The day that closes is the one the call was granted
        on: a call that crosses midnight must not close the next day (audit 6)."""
        if answer.reason == "error_max_budget_usd" and grant.amount < ticket_left:
            company = self._context.company
            company.day.exhaust(grant.day)
            raise DayPause(f"the daily cap max_usd_per_day of US$ {company.settings.max_usd_per_day:g} is reached")

    def _values(self) -> Mapping[str, str]:
        company = self._context.company
        return {
            "company": company.name,
            "repo": self._context.item.repo,
            "knowledge": self._known(company.knowledge_file()),
            "index": self._known(company.index_file()),
            "state": self._known(company.state_file()),
            "page": self._known(Path(company.page_file(self._context.item.key))),
            "rules": self._rules().baseline_block(),
        }


__all__ = [
    "AgentSteps",
    "Answers",
    "Budget",
    "BudgetExhausted",
    "CommentAnswer",
    "LimitHit",
    "Notes",
    "Plan",
    "Prompts",
    "Review",
    "ServeCache",
    "TaskContext",
    "TaskPrep",
    "TaskRefs",
]
