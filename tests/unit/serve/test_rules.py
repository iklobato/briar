"""Rule sets of `briar serve` (decisions Q1 to Q23 of `docs/decisions/2026-11-18-serve-quality-rules.md`):
the file of a set, the sets that ship with briar, which sets apply to a task, and how they reach the
prompts of the agent. No network: real git against a local origin and a scripted agent."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

import pytest

from briar.errors import CliError
from briar.iac.runbook.models import ServeSettings
from briar.serve.rules import PythonWorktree, RuleBook, RuleFile, ShippedRules
from briar.serve.task import Prompts
from briar.serve.work import ServeCompanies
from tests.unit.serve.fakes import Companies
from tests.unit.serve.ticket_fakes import Files, Items, Plans, ScriptedAgent, Setup, Setups, Step

SET = """# demo

## Baseline
Write small methods.

### Why
Because they are easy to read.

## Review
Check the diff.

## Review: database
Check the queries.
"""
FENCED = "## Baseline\nExample:\n```\n## Review\nnot a heading\n```\n## Review\nCheck it.\n"
GOLDEN = Path(__file__).parent / "golden"
HEADER = "Rules turned on for this repo"
PRECEDENCE = "never reject a line for following a rule"
PYPROJECT = "[project]\nname = 'demo'\n"
SET_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
# What the review of `code` must carry: briar has no hook and no gate, and these are the rules the
# first real run broke, so the model reads them in the checklist (decision Q4).
MECHANICAL = ("20 lines", "elif", "type hint", "except exception", "magic number", "nesting", "print", "frozen")


class Blocks:
    """The `serve:` block and the prompt words the tests fill in."""

    def settings(self, extra: Dict[str, Any]) -> ServeSettings:
        return ServeSettings.model_validate({**Companies.REQUIRED, "repos": ["acme/app", "acme/other"], **extra})

    def values(self, rules: str) -> Dict[str, str]:
        """Every word a prompt file is filled with; `rules` is what is turned on ("" is nothing)."""
        return {
            "company": "acme",
            "repo": "acme/app",
            "knowledge": "k.md",
            "index": "i.md",
            "state": "s.md",
            "key": "ENG-1",
            "title": "t",
            "plan": "p",
            "diff": "d",
            "earlier": "e",
            "rules": rules,
        }


class TestRuleFile:
    def test_reads_a_baseline_a_review_and_a_topic_review(self) -> None:
        rule = RuleFile("demo", SET).parse()

        assert rule.baseline == "Write small methods.\n\n### Why\nBecause they are easy to read."
        assert rule.review.index("Check the diff.") < rule.review.index("Check the queries.")

    def test_a_repeated_section_is_refused(self) -> None:
        with pytest.raises(CliError, match=r"demo\.md: two `## Baseline` sections"):
            RuleFile("demo", "## Baseline\na\n## Review\nb\n## Baseline\nc\n").parse()

    def test_an_unknown_section_is_refused(self) -> None:
        with pytest.raises(CliError, match=r"demo\.md: unknown section `## Notes`"):
            RuleFile("demo", "## Baseline\na\n## Review\nb\n## Notes\nc\n").parse()

    def test_a_heading_inside_a_code_fence_is_part_of_the_text(self) -> None:
        rule = RuleFile("demo", FENCED).parse()

        assert "## Review\nnot a heading" in rule.baseline
        assert rule.review == "Check it."

    @pytest.mark.parametrize("text, missing", [("## Review\nx\n", "Baseline"), ("## Baseline\nx\n", "Review")])
    def test_a_set_with_no_baseline_or_no_review_is_refused(self, text: str, missing: str) -> None:
        with pytest.raises(CliError, match=rf"demo\.md: has no `## {missing}`"):
            RuleFile("demo", text).parse()


class TestShippedRules:
    def test_the_code_set_ships_with_both_sections(self) -> None:
        code = ShippedRules().sets()["code"]

        assert code.baseline != "" and code.review != ""

    def test_the_names_of_shipped_sets_are_valid_rule_set_names(self) -> None:
        assert all(SET_NAME.match(name) for name in ShippedRules().sets())

    def test_the_review_of_code_carries_the_mechanical_rules(self) -> None:
        review = ShippedRules().sets()["code"].review.lower()

        assert [phrase for phrase in MECHANICAL if phrase not in review] == []


class TestRuleBook:
    def test_an_unknown_name_stops_the_load_and_lists_the_known_ones(self) -> None:
        with pytest.raises(CliError, match=r"unknown rule set `nope`.*known: code"):
            RuleBook.of(Blocks().settings({"rules": ["nope"]}))

    def test_an_unknown_name_in_a_repo_list_stops_the_load_too(self) -> None:
        with pytest.raises(CliError, match=r"unknown rule set `nope`"):
            RuleBook.of(Blocks().settings({"rules_by_repo": {"acme/app": ["nope"]}}))

    def test_no_sets_give_empty_blocks(self, tmp_path: Path) -> None:
        rules = RuleBook.of(Blocks().settings({})).for_task("acme/app", tmp_path)

        assert (rules.baseline_block(), rules.review_block(), rules.used, rules.skipped) == ("", "", (), ())

    def test_code_on_a_python_worktree(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(PYPROJECT)
        code = ShippedRules().sets()["code"]

        rules = RuleBook.of(Blocks().settings({"rules": ["code"]})).for_task("acme/app", tmp_path)

        assert rules.used == ("code",)
        assert HEADER in rules.baseline_block() and code.baseline in rules.baseline_block()
        assert PRECEDENCE in rules.review_block() and code.review in rules.review_block()

    def test_code_is_skipped_in_a_worktree_with_no_python_manifest(self, tmp_path: Path) -> None:
        rules = RuleBook.of(Blocks().settings({"rules": ["code"]})).for_task("acme/app", tmp_path)

        assert (rules.baseline_block(), rules.review_block(), rules.used, rules.skipped) == ("", "", (), ("code: no Python manifest",))

    def test_the_list_of_a_repo_replaces_the_company_list(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(PYPROJECT)
        book = RuleBook.of(Blocks().settings({"rules": ["code"], "rules_by_repo": {"acme/other": []}}))

        assert (book.for_task("acme/app", tmp_path).used, book.for_task("acme/other", tmp_path).used) == (("code",), ())

    def test_a_block_starts_with_a_blank_line_and_has_none_at_the_end(self, tmp_path: Path) -> None:
        """So an empty value leaves a prompt as it was, and a full one adds one clean block."""
        (tmp_path / "pyproject.toml").write_text(PYPROJECT)

        rules = RuleBook.of(Blocks().settings({"rules": ["code"]})).for_task("acme/app", tmp_path)

        for block in (rules.baseline_block(), rules.review_block()):
            assert block.startswith("\n\n") and not block.endswith("\n")


class TestPythonWorktree:
    def test_a_manifest_of_the_python_detector_makes_it_python(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(PYPROJECT)

        assert PythonWorktree(tmp_path).is_python() is True

    def test_no_manifest_is_not_python(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")

        assert PythonWorktree(tmp_path).is_python() is False


class TestPromptsAreUnchangedWithNoRules:
    @pytest.mark.parametrize("name", ["system", "review"])
    def test_a_prompt_is_the_text_it_was_in_v2(self, name: str) -> None:
        """The golden files are the prompts of `briar/v2` before `{rules}`: with nothing turned on
        every agent call is byte for byte what it was."""
        golden = (GOLDEN / f"{name}.md").read_text().format_map(Blocks().values(""))

        assert Prompts().text(name, Blocks().values("")) == golden

    def test_a_block_lands_after_the_last_sentence_of_the_system_prompt(self) -> None:
        assert Prompts().text("system", Blocks().values("\n\nR")).endswith("after you finish.\n\nR\n")

    def test_a_block_lands_after_the_findings_sentence_of_the_review_prompt(self) -> None:
        assert "and what to change.\n\nR\n\nA change made" in Prompts().text("review", Blocks().values("\n\nR"))


class TestWiring:
    RUNBOOK = (
        "version: 1\ncompanies:\n  acme:\n    serve:\n      repos: [acme/app]\n      model: claude-test\n"
        "      max_usd: 1\n      max_loops: 1\n      ci_wait_minutes: 1\n      max_review_rounds: 1\n"
    )

    def load(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rules: str) -> Any:
        (tmp_path / "runbooks").mkdir()
        (tmp_path / "runbooks" / "acme.yaml").write_text(self.RUNBOOK + rules)
        monkeypatch.chdir(tmp_path)
        return ServeCompanies(Path("runbooks"), {}).load()

    def test_a_runbook_turns_a_set_on_through_the_real_loader(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (company,) = self.load(tmp_path, monkeypatch, "      rules: [code]\n")

        assert company.rules.names_for("acme/app") == ["code"]

    def test_an_unknown_set_in_a_runbook_stops_serve_at_load(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(CliError, match=r"unknown rule set `nope`"):
            self.load(tmp_path, monkeypatch, "      rules: [nope]\n")

    def test_a_runbook_with_no_rules_has_an_empty_book(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (company,) = self.load(tmp_path, monkeypatch, "")

        assert company.rules.names_for("acme/app") == []


class TestAgentCalls:
    """A whole ticket (plan, build, self review) through the real routine, on a local git origin."""

    def ticket_of_a_python_repo(self, setups: Setups, extra: Dict[str, Any]) -> ScriptedAgent:
        setup = setups.make(extra)
        setup.origin.push_commit("main", {"pyproject.toml": PYPROJECT})
        return self.worked_by(setup)

    def ticket_of_a_repo_with_no_manifest(self, setups: Setups, extra: Dict[str, Any]) -> ScriptedAgent:
        return self.worked_by(setups.make(extra))

    def worked_by(self, setup: Setup) -> ScriptedAgent:
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()])
        setup.work_on(agent, Items().jira())
        return agent

    def test_every_call_and_the_review_get_the_rules(self, setups: Setups) -> None:
        agent = self.ticket_of_a_python_repo(setups, {"rules": ["code"]})

        review = [request for request in agent.requests if request.prompt.startswith("Review this change")]
        assert [HEADER in request.system_prompt for request in agent.requests] == [True, True, True]
        assert len(review) == 1 and PRECEDENCE in review[0].prompt

    def test_a_repo_with_no_python_manifest_gets_nothing(self, setups: Setups) -> None:
        agent = self.ticket_of_a_repo_with_no_manifest(setups, {"rules": ["code"]})

        assert [HEADER in request.system_prompt for request in agent.requests] == [False, False, False]
        assert all(PRECEDENCE not in request.prompt for request in agent.requests)

    def test_with_no_rules_nothing_is_added(self, setups: Setups) -> None:
        agent = self.ticket_of_a_python_repo(setups, {})

        assert [HEADER in request.system_prompt for request in agent.requests] == [False, False, False]
