"""The prompt files moved from the 1.x personas and rules ship in the package (C21)."""

from __future__ import annotations

from importlib.resources import files
from typing import List

import pytest
import yaml

FRONT_MATTER = "---"
FRONT_MATTER_SPLITS = 2  # "", header, body
ROLES_AND_TASKS = ("roles/engineer.md", "tasks/answer-pr-comments.md", "tasks/fix-ci.md", "tasks/resolve-conflict.md")
RULES = (
    "ai_prefix_on_comments.md",
    "commit_as_human.md",
    "minimum_correct_fix.md",
    "no_force_push.md",
    "no_new_pr_creation.md",
    "no_workflow_file_edits.md",
    "read_all_comments_first.md",
    "skip_approved_green_prs.md",
)


class PromptFile:
    """One packaged prompt file's text, split into its YAML front-matter and its body."""

    def __init__(self, text: str) -> None:
        self._text = text

    @classmethod
    def packaged(cls, relative: str) -> PromptFile:
        return cls(files("briar.prompts").joinpath(relative).read_text(encoding="utf-8"))

    def meta(self) -> dict:
        _, header, _ = self._text.split(FRONT_MATTER, FRONT_MATTER_SPLITS)
        return yaml.safe_load(header)

    def body(self) -> str:
        return self._text.split(FRONT_MATTER, FRONT_MATTER_SPLITS)[FRONT_MATTER_SPLITS]


class TestRoleAndTaskPrompts:
    @pytest.mark.parametrize("relative", ROLES_AND_TASKS)
    def test_has_its_1x_fields_and_sections(self, relative: str) -> None:
        prompt = PromptFile.packaged(relative)

        assert set(prompt.meta()) == {"from_1x_archetype", "description", "max_iter", "consumes"}
        assert [line for line in prompt.body().splitlines() if line.startswith("## ")] == ["## Role", "## Goal", "## Instructions"]

    @pytest.mark.parametrize("relative", ROLES_AND_TASKS)
    def test_keeps_the_target_placeholder(self, relative: str) -> None:
        assert "{target}" in PromptFile.packaged(relative).body()


class TestRulePrompts:
    @pytest.mark.parametrize("name", RULES)
    def test_rule_has_name_severity_and_targets(self, name: str) -> None:
        meta = PromptFile.packaged(f"rules/{name}").meta()

        assert meta["name"] and meta["severity"] and isinstance(meta["applies_to"], List)

    def test_every_rule_file_is_listed(self) -> None:
        shipped = sorted(entry.name for entry in files("briar.prompts").joinpath("rules").iterdir() if entry.name.endswith(".md"))

        assert shipped == sorted(RULES)
