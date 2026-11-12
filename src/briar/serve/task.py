"""The parts of one ticket task of `briar serve` (spec 003 PR 2): where it works
(`TaskContext`, made by `TaskPrep`), what it may spend (`Budget`, S23), the plan, and
the agent steps (plan, build, fix, self-review; S18 to S20)."""

from __future__ import annotations

import importlib.resources
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, List, Mapping, Tuple

from briar.errors import CliError
from briar.serve.agent import Access, AgentAnswer, AgentRequest, AgentRunner
from briar.serve.git import GitIdentity, RepoCheckout, Shell
from briar.serve.state import WorkSource
from briar.serve.work import ServeCompany, WorkItem, WorkSources


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


class TaskPrep:
    """Makes the context of a ticket task: the clone, the worktree, the branch and the title
    (S27), the ticket text, and who the commits are from (S2)."""

    UNSAFE = re.compile(r"[^A-Za-z0-9._/-]+")

    def __init__(self, sources: WorkSources, shell: Shell, cache: Path) -> None:
        self._sources = sources
        self._shell = shell
        self._cache = cache

    def context(self, company: ServeCompany, item: WorkItem) -> TaskContext:
        github = self._sources.github()
        token = company.logins.github().token()
        checkout = RepoCheckout(self._shell, self._cache, item.repo)
        checkout.update(token)
        fields = {"key": item.ref(), "title": item.title, "number": item.number()}
        branch = self.UNSAFE.sub("-", company.settings.branch.format_map(fields)).strip("-")
        base = github.default_branch(item.repo)
        worktree = checkout.worktree(branch, base, self._cache / "worktrees" / company.name / branch.replace("/", "__"))
        title = company.settings.title.format_map(fields)
        identity = GitIdentity(github.who_am_i())
        return TaskContext(item, company, worktree, checkout, base, branch, title, self._describe(item), identity, token, self._sources)

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

    SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "string"}},
            "tests": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "steps"],
    }

    @classmethod
    def from_answer(cls, answer: AgentAnswer) -> "Plan":
        found = answer.structured
        if not found:
            return cls(answer.text.strip() or "(the agent gave no plan)")
        return cls(str(found.get("summary", "")), *(tuple(str(entry) for entry in found.get(name, [])) for name in ("steps", "tests", "risks")))

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
        verify = "\n".join(f"- `{command}`" for command in self._context.company.settings.verify) or "- (no verify commands set)"
        await self._ask(Prompts().text("build", {**self._fields(), "plan": plan.markdown(), "verify": verify}), {}, Access.EDIT)

    async def fix(self, what: str, details: str) -> None:
        await self._ask(Prompts().text("fix", {**self._fields(), "what": what, "details": details}), {}, Access.EDIT)

    async def review(self, plan: Plan, diff: str) -> Review:
        prompt = Prompts().text("review", {**self._fields(), "plan": plan.markdown(), "diff": diff[: self.DIFF_LIMIT]})
        return Review.from_answer(await self._ask(prompt, Review.SCHEMA, Access.READ))

    def _fields(self) -> Mapping[str, str]:
        return {"key": self._context.item.ref(), "title": self._context.item.title}

    async def _ask(self, prompt: str, schema: Mapping[str, Any], access: Access) -> AgentAnswer:
        company = self._context.company
        values = {"company": company.name, "repo": self._context.item.repo, "knowledge": str(company.knowledge_file()), "state": str(company.state_file())}
        request = AgentRequest(prompt, Prompts().text("system", values), self._context.worktree, self._budget.left_usd(), schema, access)
        answer = await self._agent.ask(request)
        self._budget.spend(answer)
        if answer.failed:
            raise LimitHit(f"the agent stopped: {answer.reason or 'no reason given'}")
        return answer


__all__ = ["AgentSteps", "Budget", "LimitHit", "Plan", "Prompts", "Review", "ServeCache", "TaskContext", "TaskPrep"]
