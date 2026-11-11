"""RunbookExtractor — walks a RunbookFile, runs extractors, writes the
per-company knowledge blob via a compare-and-set store.

No network / no real store: we inject fake registry / composer / store
seams. ``RunbookExtractor.extract`` lazy-imports ``EXTRACTORS``,
``KnowledgeComposer`` and ``make_store`` at call time, so the tests patch
those module attributes; ``_run_schedule`` takes them as explicit kwargs,
so we hand it fakes directly to exercise the failure branches.

We assert the operator-visible ``ExtractRow`` results (status text +
blob name) and store side effects — an off-by-one, a flipped
stop-vs-continue, or a dropped error branch makes these fail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from pytest_mock import MockerFixture

from briar.errors import ConfigError
from briar.extract.base import ExtractedSection
from briar.iac.runbook.executor import ExtractRow, RunbookExtractor, RunbookLoader
from briar.iac.runbook.models import CompanyEntry, ExtractEntry, KnowledgeBinding, RunbookFile, ScheduleEntry
from tests.unit.fake_logins import FakeLogins

# ── Fakes ────────────────────────────────────────────────────────────


class _FakeExtractor:
    """Minimal stand-in for a registered KnowledgeExtractor.

    ``available`` toggles is_available; ``section`` is what extract returns;
    ``raises`` makes extract throw (exercising the per-extractor catch).
    """

    def __init__(self, *, section: Optional[ExtractedSection] = None, available: bool = True, raises: Optional[Exception] = None) -> None:
        self._section = section if section is not None else ExtractedSection(title="Infra", body="body")
        self._available = available
        self._raises = raises
        self.extract_calls = 0

    def add_arguments(self, parser) -> None:  # noqa: ANN001
        parser.add_argument("--company", default="")

    def is_available(self, ns) -> bool:  # noqa: ANN001
        return self._available

    def extract(self, ns) -> ExtractedSection:  # noqa: ANN001
        self.extract_calls += 1
        if self._raises is not None:
            raise self._raises
        return self._section


class _FakeRegistry(dict):
    """Dict-shaped registry: ``.get(name)`` returns the extractor or None."""


class _FakeOutcome:
    def __init__(self, *, wrote: bool, byte_count: int = 42, new_hash: str = "abcdef0123456789", prev_hash: str = "") -> None:
        self.wrote = wrote
        self.byte_count = byte_count
        self.new_hash = new_hash
        self.prev_hash = prev_hash


class _FakeStore:
    def __init__(self, outcome: _FakeOutcome, *, raises: Optional[Exception] = None) -> None:
        self._outcome = outcome
        self._raises = raises
        self.put_calls: List[Dict[str, Any]] = []

    def put_if_changed(self, blob_name: str, content: str, category: str = "") -> _FakeOutcome:
        self.put_calls.append({"blob_name": blob_name, "content": content, "category": category})
        if self._raises is not None:
            raise self._raises
        return self._outcome


class _FakeComposer:
    rendered: List[Dict[str, Any]] = []

    @classmethod
    def markdown(cls, *, company: str, sections: List[ExtractedSection]) -> str:
        cls.rendered.append({"company": company, "sections": list(sections)})
        return f"# {company}\n" + "\n".join(s.title for s in sections)

    @classmethod
    def inventory(cls, *, company: str, sections: List[ExtractedSection]) -> str:
        return '{"company": "%s", "sections": %d}' % (company, len(sections))


def _make_store_returning(store: _FakeStore):
    def _make_store(kind: str, *, file_root, binding):  # noqa: ANN001
        return store

    return _make_store


def _schedule(task: str = "extractors", names: Optional[List[str]] = None) -> ScheduleEntry:
    names = names if names is not None else ["fake"]
    return ScheduleEntry(task=task, every="day at 03:17", extract=[ExtractEntry.model_construct(name=n, args={}) for n in names])


# ── _run_schedule: phase-by-phase ────────────────────────────────────


class TestRunScheduleHappyPath:
    def test_writes_and_records_wrote_row(self) -> None:
        rows: List[ExtractRow] = []
        registry = _FakeRegistry(fake=_FakeExtractor(section=ExtractedSection(title="Infra", body="b")))
        store = _FakeStore(_FakeOutcome(wrote=True, byte_count=128))
        _FakeComposer.rendered = []

        RunbookExtractor._run_schedule(
            company_name="acme",
            schedule=_schedule(),
            binding=KnowledgeBinding(store="file", name="acme.md"),
            registry=registry,
            composer=_FakeComposer,
            make_store=_make_store_returning(store),
            rows=rows,
            logins=FakeLogins().logins(),
        )

        assert len(rows) == 1
        assert rows[0].company == "acme"
        assert rows[0].status == "wrote 128 bytes via store=file"
        assert rows[0].output == "acme.md"
        # Compare-and-set actually called with the composed markdown.
        assert store.put_calls[0]["blob_name"] == "acme.md"
        assert store.put_calls[0]["category"] == "knowledge"
        assert "Infra" in store.put_calls[0]["content"]

    def test_unchanged_records_skip_row_with_hash_prefix(self) -> None:
        rows: List[ExtractRow] = []
        registry = _FakeRegistry(fake=_FakeExtractor())
        store = _FakeStore(_FakeOutcome(wrote=False, byte_count=99, new_hash="deadbeefcafef00d"))

        RunbookExtractor._run_schedule(
            company_name="acme",
            schedule=_schedule(),
            binding=KnowledgeBinding(store="file", name="acme.md"),
            registry=registry,
            composer=_FakeComposer,
            make_store=_make_store_returning(store),
            rows=rows,
            logins=FakeLogins().logins(),
        )
        assert rows[0].status == "skipped (unchanged, 99 bytes, hash=deadbeef)"

    def test_non_default_task_suffixes_blob_name(self) -> None:
        rows: List[ExtractRow] = []
        registry = _FakeRegistry(fake=_FakeExtractor())
        store = _FakeStore(_FakeOutcome(wrote=True, byte_count=10))

        RunbookExtractor._run_schedule(
            company_name="acme",
            schedule=_schedule(task="nightly"),
            binding=KnowledgeBinding(store="file", name="acme.md"),
            registry=registry,
            composer=_FakeComposer,
            make_store=_make_store_returning(store),
            rows=rows,
            logins=FakeLogins().logins(),
        )
        # ".md" base → "<base>.<task>.md"
        assert store.put_calls[0]["blob_name"] == "acme.nightly.md"
        assert rows[0].output == "acme.nightly.md"


class TestRunScheduleInventory:
    def test_disabled_by_default_writes_single_blob(self) -> None:
        rows: List[ExtractRow] = []
        registry = _FakeRegistry(fake=_FakeExtractor())
        store = _FakeStore(_FakeOutcome(wrote=True, byte_count=10))

        RunbookExtractor._run_schedule(
            company_name="acme",
            schedule=_schedule(),
            binding=KnowledgeBinding(store="file", name="acme.md"),
            registry=registry,
            composer=_FakeComposer,
            make_store=_make_store_returning(store),
            rows=rows,
            logins=FakeLogins().logins(),
        )
        # No config.inventory → only the knowledge blob is written.
        assert len(store.put_calls) == 1
        assert len(rows) == 1

    def test_enabled_writes_inventory_companion(self) -> None:
        rows: List[ExtractRow] = []
        registry = _FakeRegistry(fake=_FakeExtractor())
        store = _FakeStore(_FakeOutcome(wrote=True, byte_count=10))

        RunbookExtractor._run_schedule(
            company_name="acme",
            schedule=_schedule(),
            binding=KnowledgeBinding(store="file", name="acme.md", config={"inventory": "true"}),
            registry=registry,
            composer=_FakeComposer,
            make_store=_make_store_returning(store),
            rows=rows,
            logins=FakeLogins().logins(),
        )
        assert len(store.put_calls) == 2
        inv = store.put_calls[1]
        assert inv["blob_name"] == "acme.inventory.json"
        assert inv["category"] == "inventory"
        assert inv["content"] == '{"company": "acme", "sections": 1}'
        assert rows[1].status == "inventory wrote (10 bytes)"
        assert rows[1].output == "acme.inventory.json"

    def test_enabled_derives_category_style_name(self) -> None:
        rows: List[ExtractRow] = []
        registry = _FakeRegistry(fake=_FakeExtractor())
        store = _FakeStore(_FakeOutcome(wrote=True, byte_count=10))

        RunbookExtractor._run_schedule(
            company_name="acme",
            schedule=_schedule(),
            binding=KnowledgeBinding(store="file", name="knowledge:acme", config={"inventory": "on"}),
            registry=registry,
            composer=_FakeComposer,
            make_store=_make_store_returning(store),
            rows=rows,
            logins=FakeLogins().logins(),
        )
        assert store.put_calls[1]["blob_name"] == "inventory:acme"

    def test_inventory_failure_is_best_effort(self) -> None:
        rows: List[ExtractRow] = []
        registry = _FakeRegistry(fake=_FakeExtractor())

        class _FlakyStore:
            """Succeeds on the knowledge write, raises on the inventory one."""

            def __init__(self) -> None:
                self.put_calls: List[Dict[str, Any]] = []

            def put_if_changed(self, blob_name: str, content: str, category: str = "") -> _FakeOutcome:
                self.put_calls.append({"blob_name": blob_name, "category": category})
                if category == "inventory":
                    raise RuntimeError("boom")
                return _FakeOutcome(wrote=True, byte_count=10)

        store = _FlakyStore()
        RunbookExtractor._run_schedule(
            company_name="acme",
            schedule=_schedule(),
            binding=KnowledgeBinding(store="file", name="acme.md", config={"inventory": "yes"}),
            registry=registry,
            composer=_FakeComposer,
            make_store=_make_store_returning(store),
            rows=rows,
            logins=FakeLogins().logins(),
        )
        # Main knowledge write still succeeded; inventory failure is its own row.
        assert rows[0].status == "wrote 10 bytes via store=file"
        assert rows[1].status == "inventory failed (see traceback)"
        assert rows[1].output == "acme.inventory.json"


class TestRunScheduleEmptyAndFailures:
    def test_zero_sections_records_empty_row_and_skips_store(self) -> None:
        rows: List[ExtractRow] = []
        # is_available False → no section collected → "empty (no sections)".
        registry = _FakeRegistry(fake=_FakeExtractor(available=False))
        store = _FakeStore(_FakeOutcome(wrote=True))

        RunbookExtractor._run_schedule(
            company_name="acme",
            schedule=_schedule(),
            binding=KnowledgeBinding(store="file", name="acme.md"),
            registry=registry,
            composer=_FakeComposer,
            make_store=_make_store_returning(store),
            rows=rows,
            logins=FakeLogins().logins(),
        )
        assert rows[0].status == "empty (no sections)"
        assert store.put_calls == []  # never opened/wrote

    def test_collect_sections_raising_records_failure_and_notifies(self, caplog_briar) -> None:
        rows: List[ExtractRow] = []
        store = _FakeStore(_FakeOutcome(wrote=True))

        # Make _collect_sections itself raise (not the per-extractor catch,
        # which swallows) by handing a registry whose .get explodes.
        class _BoomRegistry:
            def get(self, name):  # noqa: ANN001
                raise RuntimeError("registry exploded")

        RunbookExtractor._run_schedule(
            company_name="acme",
            schedule=_schedule(),
            binding=KnowledgeBinding(store="file", name="acme.md"),
            registry=_BoomRegistry(),
            composer=_FakeComposer,
            make_store=_make_store_returning(store),
            rows=rows,
            logins=FakeLogins().logins(),
        )
        assert len(rows) == 1
        assert rows[0].status.startswith("failed (collect_sections raised")
        assert store.put_calls == []

    def test_store_open_failure_records_failure_row(self) -> None:
        rows: List[ExtractRow] = []
        registry = _FakeRegistry(fake=_FakeExtractor())

        def _boom_make_store(kind, *, file_root, binding):  # noqa: ANN001
            raise RuntimeError("disk full")

        RunbookExtractor._run_schedule(
            company_name="acme",
            schedule=_schedule(),
            binding=KnowledgeBinding(store="file", name="acme.md"),
            registry=registry,
            composer=_FakeComposer,
            make_store=_boom_make_store,
            rows=rows,
            logins=FakeLogins().logins(),
        )
        assert rows[0].status.startswith("failed (store open raised: file")

    def test_put_if_changed_failure_records_failure_row(self) -> None:
        rows: List[ExtractRow] = []
        registry = _FakeRegistry(fake=_FakeExtractor())
        store = _FakeStore(_FakeOutcome(wrote=True), raises=RuntimeError("write conflict"))

        RunbookExtractor._run_schedule(
            company_name="acme",
            schedule=_schedule(),
            binding=KnowledgeBinding(store="file", name="acme.md"),
            registry=registry,
            composer=_FakeComposer,
            make_store=_make_store_returning(store),
            rows=rows,
            logins=FakeLogins().logins(),
        )
        assert rows[0].status.startswith("failed (put_if_changed raised: acme.md")


# ── _collect_sections: per-extractor contract ────────────────────────


class TestCollectSections:
    def test_collects_in_order_and_skips_empty(self) -> None:
        entries = [
            ExtractEntry.model_construct(name="a", args={}),
            ExtractEntry.model_construct(name="b", args={}),
            ExtractEntry.model_construct(name="c", args={}),
        ]
        registry = _FakeRegistry(
            a=_FakeExtractor(section=ExtractedSection(title="A")),
            b=_FakeExtractor(section=ExtractedSection()),  # empty → skipped
            c=_FakeExtractor(section=ExtractedSection(title="C")),
        )
        sections = RunbookExtractor._collect_sections(entries, registry, logins=FakeLogins().logins())
        assert [s.title for s in sections] == ["A", "C"]

    def test_unknown_extractor_is_skipped(self) -> None:
        entries = [ExtractEntry.model_construct(name="missing", args={})]
        registry = _FakeRegistry()  # .get("missing") is None
        assert RunbookExtractor._collect_sections(entries, registry, logins=FakeLogins().logins()) == []

    def test_unavailable_extractor_is_skipped(self) -> None:
        entries = [ExtractEntry.model_construct(name="a", args={})]
        registry = _FakeRegistry(a=_FakeExtractor(available=False))
        assert RunbookExtractor._collect_sections(entries, registry, logins=FakeLogins().logins()) == []

    def test_extractor_raising_is_swallowed_and_others_continue(self) -> None:
        entries = [
            ExtractEntry.model_construct(name="boom", args={}),
            ExtractEntry.model_construct(name="ok", args={}),
        ]
        registry = _FakeRegistry(
            boom=_FakeExtractor(raises=RuntimeError("extract failed")),
            ok=_FakeExtractor(section=ExtractedSection(title="OK")),
        )
        sections = RunbookExtractor._collect_sections(entries, registry, logins=FakeLogins().logins())
        assert [s.title for s in sections] == ["OK"]

    def test_company_injected_into_namespace(self) -> None:
        captured = {}

        class _CompanyAwareExtractor(_FakeExtractor):
            def extract(self, ns):  # noqa: ANN001
                captured["company"] = ns.company
                return ExtractedSection(title="X")

        entries = [ExtractEntry.model_construct(name="a", args={})]
        registry = _FakeRegistry(a=_CompanyAwareExtractor())
        RunbookExtractor._collect_sections(entries, registry, logins=FakeLogins(company="globex").logins())
        assert captured["company"] == "globex"

    def test_explicit_company_arg_overrides_injection(self) -> None:
        captured = {}

        class _CompanyAwareExtractor(_FakeExtractor):
            def extract(self, ns):  # noqa: ANN001
                captured["company"] = ns.company
                return ExtractedSection(title="X")

        # args carry an explicit company → injection is skipped.
        entries = [ExtractEntry.model_construct(name="a", args={"company": "explicit"})]
        registry = _FakeRegistry(a=_CompanyAwareExtractor())
        RunbookExtractor._collect_sections(entries, registry, logins=FakeLogins(company="globex").logins())
        assert captured["company"] == "explicit"


# ── extract(): orchestration + task filtering ────────────────────────


def _patch_seams(mocker, *, registry, store, composer=_FakeComposer):
    import briar.extract as extract_mod
    import briar.extract.composer as composer_mod
    import briar.storage as storage_mod

    mocker.patch.object(extract_mod, "EXTRACTORS", registry)
    mocker.patch.object(composer_mod, "KnowledgeComposer", composer)
    mocker.patch.object(storage_mod, "make_store", _make_store_returning(store))


def _runbook(companies: Dict[str, CompanyEntry]) -> RunbookFile:
    return RunbookFile.model_construct(version=1, companies=companies)


class TestExtractOrchestration:
    def test_runs_every_company_schedule(self, mocker) -> None:
        registry = _FakeRegistry(fake=_FakeExtractor())
        store = _FakeStore(_FakeOutcome(wrote=True, byte_count=5))
        _patch_seams(mocker, registry=registry, store=store)

        rb = _runbook(
            {
                "acme": CompanyEntry(knowledge=KnowledgeBinding(store="file", name="acme.md"), schedules=[_schedule()]),
                "globex": CompanyEntry(knowledge=KnowledgeBinding(store="file", name="globex.md"), schedules=[_schedule()]),
            }
        )
        rows = RunbookExtractor.extract(rb)
        companies = sorted(r.company for r in rows)
        assert companies == ["acme", "globex"]
        assert all(r.status.startswith("wrote") for r in rows)

    def test_task_filter_selects_matching_schedule_only(self, mocker) -> None:
        registry = _FakeRegistry(fake=_FakeExtractor())
        store = _FakeStore(_FakeOutcome(wrote=True, byte_count=5))
        _patch_seams(mocker, registry=registry, store=store)

        rb = _runbook(
            {
                "acme": CompanyEntry(
                    knowledge=KnowledgeBinding(store="file", name="acme.md"),
                    schedules=[_schedule(task="nightly"), _schedule(task="hourly")],
                )
            }
        )
        rows = RunbookExtractor.extract(rb, task="hourly")
        # Only the hourly schedule ran → one row, blob suffixed for that task.
        assert len(rows) == 1
        assert rows[0].output == "acme.hourly.md"

    def test_no_filter_records_skipped_row_when_company_has_no_schedule(self, mocker) -> None:
        registry = _FakeRegistry(fake=_FakeExtractor())
        store = _FakeStore(_FakeOutcome(wrote=True))
        _patch_seams(mocker, registry=registry, store=store)

        rb = _runbook({"acme": CompanyEntry(knowledge=KnowledgeBinding(store="file", name="acme.md"))})
        rows = RunbookExtractor.extract(rb)  # task=None
        assert rows == [ExtractRow("acme", "-", "skipped (no schedule)", "")]

    def test_empty_string_task_is_equivalent_to_none(self, mocker) -> None:
        registry = _FakeRegistry(fake=_FakeExtractor())
        store = _FakeStore(_FakeOutcome(wrote=True))
        _patch_seams(mocker, registry=registry, store=store)

        rb = _runbook({"acme": CompanyEntry(knowledge=KnowledgeBinding(store="file", name="acme.md"))})
        rows = RunbookExtractor.extract(rb, task="")
        # "" coerced to None → same "skipped (no schedule)" as task=None.
        assert rows == [ExtractRow("acme", "-", "skipped (no schedule)", "")]

    def test_filtered_task_with_no_match_yields_no_rows(self, mocker) -> None:
        registry = _FakeRegistry(fake=_FakeExtractor())
        store = _FakeStore(_FakeOutcome(wrote=True))
        _patch_seams(mocker, registry=registry, store=store)

        rb = _runbook({"acme": CompanyEntry(knowledge=KnowledgeBinding(store="file", name="acme.md"), schedules=[_schedule(task="nightly")])})
        # A non-matching explicit filter → no schedule runs, no skipped-row
        # either (skipped-row only when task_filter is None).
        rows = RunbookExtractor.extract(rb, task="weekly")
        assert rows == []


class TestBindingResolution:
    def test_knowledge_name_takes_precedence(self) -> None:
        company = CompanyEntry(knowledge=KnowledgeBinding(name="kb.md", root="kb"), knowledge_file="ignored.md")
        binding = RunbookExtractor._binding_for(company, "acme")
        assert binding.root == "kb"
        assert binding.name == "kb.md"

    def test_falls_back_to_knowledge_file(self) -> None:
        company = CompanyEntry(knowledge_file="custom/acme.md")
        binding = RunbookExtractor._binding_for(company, "acme")
        assert binding.store == "file"
        assert binding.name == "custom/acme.md"

    def test_task_blob_name_without_md_suffix(self) -> None:
        assert RunbookExtractor._task_blob_name("acme", "nightly") == "acme.nightly"

    def test_task_blob_name_with_md_suffix(self) -> None:
        assert RunbookExtractor._task_blob_name("acme.md", "nightly") == "acme.nightly.md"


# ── RunbookSchedules.for_company: legacy + new coalescing ─────────────


class TestRunbookSchedules:
    def test_synthesises_default_schedule_from_extract_block(self) -> None:
        from briar.iac.runbook.executor import RunbookSchedules

        company = CompanyEntry(extract=[ExtractEntry.model_construct(name="fake", args={})])
        items = RunbookSchedules.for_company(company)
        assert len(items) == 1
        assert items[0].task == "extractors"
        assert items[0].every == "day at 03:17"

    def test_explicit_extractors_schedule_suppresses_synthetic_one(self) -> None:
        from briar.iac.runbook.executor import RunbookSchedules

        company = CompanyEntry(
            extract=[ExtractEntry.model_construct(name="fake", args={})],
            schedules=[_schedule(task="extractors")],
        )
        items = RunbookSchedules.for_company(company)
        # Only the explicit one — no duplicate synthetic "extractors" task.
        assert len(items) == 1
        assert items[0].every == "day at 03:17"


# ── RunbookLoader: parse + error envelopes ───────────────────────────


class TestRunbookLoader:
    def test_loads_valid_yaml(self, tmp_path, mocker) -> None:
        # Bypass extractor-name validation (the registry isn't the unit
        # under test) by validating against a construct-friendly doc.
        mocker.patch("briar.extract.EXTRACTORS", _FakeRegistry(fake=_FakeExtractor()))
        path = tmp_path / "rb.yaml"
        path.write_text("version: 1\ncompanies:\n  acme:\n    extract:\n      - name: fake\n")
        model = RunbookLoader.load(path)
        assert "acme" in model.companies

    def test_missing_file_raises_configerror(self, tmp_path) -> None:
        with pytest.raises(ConfigError, match="runbook not found"):
            RunbookLoader.load(tmp_path / "nope.yaml")

    def test_invalid_yaml_raises_configerror(self, tmp_path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("version: 1\ncompanies: [unterminated\n")
        with pytest.raises(ConfigError, match="invalid"):
            RunbookLoader.load(path)

    def test_invalid_json_raises_configerror(self, tmp_path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not valid json")
        with pytest.raises(ConfigError, match="invalid"):
            RunbookLoader.load(path)

    def test_top_level_not_a_mapping_raises(self, tmp_path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- a\n- b\n")
        with pytest.raises(ConfigError, match="top-level must be a mapping"):
            RunbookLoader.load(path)

    def test_schema_violation_raises_configerror(self, tmp_path) -> None:
        # `companies` requires min_length=1 → empty dict fails validation.
        path = tmp_path / "empty.yaml"
        path.write_text("version: 1\ncompanies: {}\n")
        with pytest.raises(ConfigError, match="invalid runbook"):
            RunbookLoader.load(path)


class TestDefaultKnowledgeBlob:
    """A company with no `knowledge.name` writes `knowledge:<company>`, the
    blob `briar extract` writes. Up to 1.1.85 the default was the path
    `./knowledge/<company>.md`, which no reader looked at."""

    def test_default_binding_is_the_knowledge_blob(self) -> None:
        binding = RunbookExtractor._binding_for(CompanyEntry(), "acme")

        assert binding.store == "file"
        assert binding.name == "knowledge:acme"

    def test_runbook_without_knowledge_name_writes_the_company_blob(self, mocker: MockerFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import briar.extract as extract_mod
        from briar.extract import EXTRACTORS
        from briar.extract.composer import KnowledgeComposer
        from briar.storage import make_store

        monkeypatch.chdir(tmp_path)
        heading = EXTRACTORS["pr-archaeology"].heading
        fake = _FakeExtractor(section=ExtractedSection(title=f"{heading} \N{EM DASH} 1 repo(s)", body="merged PR patterns"))
        mocker.patch.object(extract_mod, "EXTRACTORS", _FakeRegistry({"pr-archaeology": fake}))

        RunbookExtractor.extract(_runbook({"acme": CompanyEntry(schedules=[_schedule(names=["pr-archaeology"])])}))

        blob = make_store("file", file_root=tmp_path / "knowledge").get("knowledge:acme") or ""
        assert "merged PR patterns" in KnowledgeComposer.split_sections(blob)[heading]

    def test_explicit_knowledge_file_is_kept(self) -> None:
        binding = RunbookExtractor._binding_for(CompanyEntry(knowledge_file="./kb/acme.md"), "acme")

        assert binding.name == "./kb/acme.md"


class TestExtractCompanyFilter:
    def test_company_filter_runs_only_that_company(self, mocker) -> None:
        registry = _FakeRegistry(fake=_FakeExtractor())
        store = _FakeStore(_FakeOutcome(wrote=True, byte_count=5))
        _patch_seams(mocker, registry=registry, store=store)
        rb = _runbook(
            {
                "acme": CompanyEntry(knowledge=KnowledgeBinding(store="file", name="acme.md"), schedules=[_schedule()]),
                "globex": CompanyEntry(knowledge=KnowledgeBinding(store="file", name="globex.md"), schedules=[_schedule()]),
            }
        )

        rows = RunbookExtractor.extract(rb, "extractors", company="acme")

        assert [r.company for r in rows] == ["acme"]
        assert registry["fake"].extract_calls == 1

    def test_no_company_filter_runs_every_company(self, mocker) -> None:
        registry = _FakeRegistry(fake=_FakeExtractor())
        _patch_seams(mocker, registry=registry, store=_FakeStore(_FakeOutcome(wrote=True)))
        rb = _runbook(
            {
                "acme": CompanyEntry(knowledge=KnowledgeBinding(store="file", name="acme.md"), schedules=[_schedule()]),
                "globex": CompanyEntry(knowledge=KnowledgeBinding(store="file", name="globex.md"), schedules=[_schedule()]),
            }
        )

        rows = RunbookExtractor.extract(rb)

        assert sorted(r.company for r in rows) == ["acme", "globex"]
