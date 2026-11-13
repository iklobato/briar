"""The parts of one task of `briar serve` (spec 003 PR 2 and PR 3): where it works
(`TaskContext`, made by `TaskPrep`), what it may spend (`Budget`, S23), the plan, the
answers to review comments (V7), and the agent steps (plan, build, fix, self-review,
answer; S18 to S20)."""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, List, Mapping, Sequence, Tuple

from briar.errors import CliError
from briar.extract._provider import ReviewComment
from briar.serve.agent import Access, AgentAnswer, AgentRequest, AgentRunner
from briar.serve.git import GitIdentity, RepoCheckout, Shell
from briar.serve.state import WorkSource
from briar.serve.work import BranchName, ServeCompany, WorkItem, WorkSources


class LimitHit(CliError):
    """A limit of the task hit (S23); the message is the report."""


@dataclass(frozen=True)
class Budget:
    """The money and the fix loops one task has left (S23)."""

    max_usd: float
    max_loops: int
    spent: List[float] = field(default_factory=lambda: [0.0])
    loops: List[int] = field(default_factory=lambda: [0])

    def left_usd(self) -> float:
        return self.max_usd - self.spent[0]

    def spend(self, answer: AgentAnswer) -> None:
        self.spent[0] += answer.cost_usd
        if answer.reason == "error_max_budget_usd" or self.left_usd() <= 0:
            raise LimitHit(f"the task spent its max_usd of {self.max_usd}")

    def next_loop(self, why: str) -> None:
        self.loops[0] += 1
        if self.loops[0] > self.max_loops:
            raise LimitHit(f"max_loops ({self.max_loops}) reached while fixing: {why}")

    def summary(self) -> str:
        return f"US$ {self.spent[0]:.2f} of {self.max_usd}, {self.loops[0]} of {self.max_loops} fix loops"


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
        return TaskContext(item, company, worktree, checkout, refs.base, refs.branch, title, self._describe(item), identity, token, self._sources)

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
    # False for a ticket with nothing to change in the repo (an operations ticket, decision D1).
    needs_code: bool = True

    SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "string"}},
            "tests": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "needs_code": {"type": "boolean"},
        },
        "required": ["summary", "steps", "needs_code"],
    }

    @classmethod
    def from_answer(cls, answer: AgentAnswer) -> "Plan":
        found = answer.structured
        if not found:
            return cls(answer.text.strip() or "(the agent gave no plan)")
        steps, tests, risks = (tuple(str(entry) for entry in found.get(name, [])) for name in ("steps", "tests", "risks"))
        return cls(str(found.get("summary", "")), steps=steps, tests=tests, risks=risks, needs_code=bool(found.get("needs_code", True)))

    def markdown(self) -> str:
        parts = [self.summary, self._list("Steps", self.steps), self._list("Tests", self.tests), self._list("Risks", self.risks)]
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


class AgentSteps:
    """The agent calls of one task, each one paid from the budget."""

    # A diff longer than this is cut for the self-review prompt.
    DIFF_LIMIT = 60_000

    def __init__(self, agent: AgentRunner, context: TaskContext, budget: Budget) -> None:
        self._agent = agent
        self._context = context
        self._budget = budget

    async def plan(self) -> Plan:
        prompt = Prompts().text("plan", {**self._fields(), "description": self._context.description or "(no description)"})
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

    async def review(self, plan: Plan, diff: str, asked: Sequence[str]) -> Review:
        earlier = "\n".join(f"- {finding}" for finding in asked) or "- (none: this is the first round)"
        prompt = Prompts().text("review", {**self._fields(), "plan": plan.markdown(), "diff": diff[: self.DIFF_LIMIT], "earlier": earlier})
        return Review.from_answer(await self._ask(prompt, Review.SCHEMA, Access.READ))

    def _listed(self, comment: ReviewComment) -> str:
        where = f" on {comment.file_path}:{comment.line}" if comment.file_path else ""
        quoted = "\n".join(f"> {line}" for line in comment.body.splitlines())
        return f"id `{comment.key()}`, {comment.kind.value} comment by {comment.author}{where}:\n{quoted}"

    def _fields(self) -> Mapping[str, str]:
        return {"key": self._context.item.ref(), "title": self._context.item.title}

    def _verify(self) -> str:
        return "\n".join(f"- `{command}`" for command in self._context.company.settings.verify_for(self._context.item.repo)) or "- (no verify commands set)"

    def _known(self, path: Path) -> str:
        """A real run sent the agent to read files that did not exist yet."""
        return str(path) if path.exists() else "(none yet)"

    async def _ask(self, prompt: str, schema: Mapping[str, Any], access: Access) -> AgentAnswer:
        company = self._context.company
        values = {"company": company.name, "repo": self._context.item.repo, "knowledge": self._known(company.knowledge_file()), "state": self._known(company.state_file())}
        request = AgentRequest(prompt, Prompts().text("system", values), self._context.worktree, self._budget.left_usd(), schema, access)
        answer = await self._agent.ask(request)
        self._budget.spend(answer)
        if answer.failed:
            raise LimitHit(f"the agent stopped: {answer.reason or 'no reason given'}")
        return answer


__all__ = ["AgentSteps", "Answers", "Budget", "CommentAnswer", "LimitHit", "Plan", "Prompts", "Review", "ServeCache", "TaskContext", "TaskPrep", "TaskRefs"]
