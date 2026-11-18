"""Rule sets of `briar serve` (decisions Q1 to Q23 of `docs/decisions/2026-11-18-serve-quality-rules.md`).

A rule set is one Markdown file with the sections `## Baseline` (short text for the system prompt of every
agent call), `## Review` (the checklist of the self review) and, optionally, `## Review: <topic>`. The
runbook turns sets on, for a company or for one repo (`ServeSettings.rules`, `rules_by_repo`); nothing is
on by default. The sets that ship with briar live in `briar/prompts/rule_sets/`."""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Dict, List, Mapping, Tuple

from briar.errors import CliError
from briar.extract.language_detectors import LANGUAGE_DETECTORS
from briar.iac.runbook.models import ServeSettings


class RuleError(CliError):
    """A rule set that cannot be used: a file with a wrong section, or a name that does not exist."""


@dataclass(frozen=True)
class RuleSet:
    """One rule set: the text of its two sections. The topic reviews are inside `review`."""

    name: str
    baseline: str
    review: str


class RuleFile:
    """The text of one rule set file, read strictly: a rule file is a contract, so a repeated or an
    unknown `## ` heading is an error, a `## ` line inside a code fence is text, and `###` lines are
    text. Whatever comes before the first heading is the title and is dropped."""

    HEADING = "## "
    FENCE = "```"
    BASELINE = "Baseline"
    REVIEW = "Review"
    TOPIC = "Review: "

    def __init__(self, name: str, text: str) -> None:
        self._name = name
        self._text = text

    def parse(self) -> RuleSet:
        sections = self._sections()
        self._check(sections)
        topics = [f"### {title[len(self.TOPIC) :]}\n{body}" for title, body in sections if title.startswith(self.TOPIC)]
        review = "\n\n".join([self._body(sections, self.REVIEW), *topics])
        return RuleSet(self._name, self._body(sections, self.BASELINE), review)

    def _sections(self) -> List[Tuple[str, str]]:
        """Each `## ` heading with its text, in order; a heading inside a code fence is not one."""
        found: List[Tuple[str, List[str]]] = []
        fenced = False
        for line in self._text.splitlines():
            fenced = fenced != line.startswith(self.FENCE)
            if line.startswith(self.HEADING) and not fenced:
                found.append((line[len(self.HEADING) :].strip(), []))
                continue
            if found:
                found[-1][1].append(line)
        return [(title, "\n".join(lines).strip()) for title, lines in found]

    def _check(self, sections: List[Tuple[str, str]]) -> None:
        titles = [title for title, _ in sections]
        for title in titles:
            if title not in (self.BASELINE, self.REVIEW) and not title.startswith(self.TOPIC):
                raise RuleError(f"{self._name}.md: unknown section `## {title}` (use `## {self.BASELINE}`, `## {self.REVIEW}` or `## {self.TOPIC}<topic>`)")
            if titles.count(title) > 1:
                raise RuleError(f"{self._name}.md: two `## {title}` sections")
        for needed in (self.BASELINE, self.REVIEW):
            if needed not in titles:
                raise RuleError(f"{self._name}.md: has no `## {needed}`")

    def _body(self, sections: List[Tuple[str, str]], title: str) -> str:
        return next(body for found, body in sections if found == title)


class ShippedRules:
    """The rule sets that ship with briar, read the way `Prompts.text` reads its files."""

    PACKAGE = "briar.prompts"
    FOLDER = "rule_sets"
    SUFFIX = ".md"

    def sets(self) -> Dict[str, RuleSet]:
        folder = importlib.resources.files(self.PACKAGE).joinpath(self.FOLDER)
        files = sorted((entry for entry in folder.iterdir() if entry.name.endswith(self.SUFFIX)), key=lambda entry: entry.name)
        return {self._name(entry.name): RuleFile(self._name(entry.name), entry.read_text()).parse() for entry in files}

    def _name(self, file_name: str) -> str:
        return file_name[: -len(self.SUFFIX)]


class PythonWorktree:
    """Whether a worktree holds a Python project: a manifest of the Python detector of `extract`."""

    LANGUAGE = "python"

    def __init__(self, worktree: Path) -> None:
        self._worktree = worktree

    def is_python(self) -> bool:
        return any((self._worktree / detector.manifest).is_file() for detector in LANGUAGE_DETECTORS if detector.name == self.LANGUAGE)


@dataclass(frozen=True)
class TaskRules:
    """The rule sets that apply to one task, and the sets that were left out with the reason. The two
    blocks fill `{rules}` in `system.md` and `review.md`: each starts with a blank line and does not end
    with one, so an empty block leaves the prompt as it was."""

    BASELINE_HEADER: ClassVar[str] = (
        "Rules turned on for this repo. In the lines you add or change they win over the habits of the file you edit; "
        "code you do not touch stays as it is, and a rule break that was already in the file is not yours to fix."
    )
    REVIEW_HEADER: ClassVar[str] = (
        "These rules are turned on for this repo. When a rule below and the habit of the file disagree on a line the diff adds, the rule wins: "
        "never reject a line for following a rule, and never ask to undo it. Judge only the lines the diff adds or changes. "
        "A rule break that was already in the file before the change is not a finding, even in a method the change touches. "
        "Reject only a clear violation, with at most 10 findings and nothing that is only a matter of taste."
    )

    sets: Tuple[RuleSet, ...]
    skipped: Tuple[str, ...] = ()

    @property
    def used(self) -> Tuple[str, ...]:
        return tuple(rule.name for rule in self.sets)

    def baseline_block(self) -> str:
        return self._block(self.BASELINE_HEADER, [rule.baseline for rule in self.sets])

    def review_block(self) -> str:
        return self._block(self.REVIEW_HEADER, [rule.review for rule in self.sets])

    def _block(self, header: str, bodies: List[str]) -> str:
        return "\n\n" + "\n\n".join([header, *bodies]) if bodies else ""


class RuleBook:
    """The sets a company knows and the names its runbook turns on (decision Q9). `RuleBook.of` is the
    one way to build it from a runbook, so a test and a real `serve` go through the same code."""

    # The sets that are about Python: left out of a task whose worktree holds no Python project.
    PYTHON_ONLY: ClassVar[Tuple[str, ...]] = ("code", "tests")

    def __init__(self, sets: Mapping[str, RuleSet], default: Tuple[str, ...], by_repo: Mapping[str, Tuple[str, ...]]) -> None:
        self._sets = sets
        self._default = default
        self._by_repo = by_repo

    @classmethod
    def of(cls, settings: ServeSettings) -> "RuleBook":
        """The book of a runbook company; a name that is not a set stops `serve` at load. A runbook that
        names no set gets the empty book without reading the folder of the shipped sets, so with the
        rules off nothing here can fail."""
        if not settings.rules and not any(settings.rules_by_repo.values()):
            return cls.empty()
        book = cls(ShippedRules().sets(), tuple(settings.rules), {repo: tuple(names) for repo, names in settings.rules_by_repo.items()})
        book.require()
        return book

    @classmethod
    def empty(cls) -> "RuleBook":
        return cls({}, (), {})

    def names_for(self, repo: str) -> List[str]:
        """The set names the runbook turns on for `repo`: its own list, else the company list."""
        return list(self._by_repo.get(repo, self._default))

    def require(self) -> None:
        wanted = [("rules", name) for name in self._default] + [(f"rules_by_repo.{repo}", name) for repo, names in self._by_repo.items() for name in names]
        unknown = [(where, name) for where, name in wanted if name not in self._sets]
        if unknown:
            where, name = unknown[0]
            raise RuleError(f"serve: unknown rule set `{name}` in `{where}`; known: {', '.join(sorted(self._sets))}")

    def for_task(self, repo: str, worktree: Path) -> TaskRules:
        names = self.names_for(repo)
        if not names:
            return TaskRules(())
        python = PythonWorktree(worktree).is_python()
        skipped = tuple(f"{name}: no Python manifest" for name in names if name in self.PYTHON_ONLY and not python)
        return TaskRules(tuple(self._sets[name] for name in names if python or name not in self.PYTHON_ONLY), skipped)


__all__ = ["PythonWorktree", "RuleBook", "RuleError", "RuleFile", "RuleSet", "ShippedRules", "TaskRules"]
