"""End-to-end integration: real briar commands driving real LOCAL backends.

No network, no mocked stores or servers. Each test runs the REAL command
through the `cli` fixture (which calls `briar.cli.main(...)`) against a
real file/config backend rooted in a tmp dir, then asserts the FINAL
observable behavior: exit code, parsed stdout, files written on disk
(read back), and config/store state changed.

The `cli` fixture invokes the full startup path (credential bootstrap,
default-journal install, telemetry install). `env_sandbox` (autouse in
tests/conftest.py) strips every credential-shaped env var, so:
  * Infisical bootstrap `is_available()` is False -> no network.
  * provider-backed extractors report missing creds deterministically.
`BRIAR_SECRETS_FILE` is already redirected per-test by `env_sandbox`.

These cover: scaffold implementation / pr-fixes, secrets (envfile store
+ bootstrap), telemetry off/full/status, context put/get/list/delete,
journal list/show/export, dashboard --once, runbook extract/sweep.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


# ─────────────────────── shared env helper ─────────────────────────


def _local_env(tmp_path: Path) -> dict[str, str]:
    """Pin every on-disk location the CLI startup + commands touch into
    the test's tmp dir so nothing leaks to the developer's real config.

    - BRIAR_TELEMETRY=off keeps the telemetry installer from doing work
      (and from reading the real ~/.config). The telemetry *commands*
      under test write to XDG_CONFIG_HOME, which we also pin.
    - BRIAR_JOURNAL=off disables the auto-installed default journal for
      commands where we don't want a stray session file; tests that
      assert journaling re-enable it explicitly.
    """
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "BRIAR_TELEMETRY": "off",
        "BRIAR_JOURNAL": "off",
    }


# ════════════════════════════ scaffold ══════════════════════════════


class TestScaffoldImplementationReal:
    def test_writes_wellformed_bundle_reflecting_flags(self, cli, tmp_path) -> None:
        out_file = tmp_path / "impl.json"
        result = cli(
            "scaffold",
            "implementation",
            "--prefix",
            "acme-impl",
            "--source",
            "github",
            "--owner",
            "alice",
            "--repo",
            "widgets",
            "--archetype",
            "pr-fixer",
            "--model",
            "claude-opus-4-1",
            "--llm-provider-key",
            "anthropic",
            "--out",
            str(out_file),
            env=_local_env(tmp_path),
        )
        assert result.code == 0, result.err
        assert f"wrote {out_file}" in result.out

        # Read the emitted spec back off disk and assert it is well-formed
        # AND reflects every flag we passed.
        bundle = json.loads(out_file.read_text())
        assert bundle["version"] == 1
        assert set(bundle) >= {"llm_models", "sources", "tools", "agents", "workflows", "triggers"}

        # --prefix prepends every resource key.
        agent = bundle["agents"][0]
        assert agent["key"] == "acme-impl-pr-fixer"  # prefix + archetype
        assert bundle["llm_models"][0]["key"] == "acme-impl-model"
        assert bundle["workflows"][0]["key"] == "acme-impl-workflow"

        # --source github + --owner/--repo land in the source config.
        source = bundle["sources"][0]
        assert source["kind"] == "github"
        assert source["key"] == "acme-impl-gh-issues"
        assert source["config"]["owner"] == "alice"
        assert source["config"]["repo"] == "alice/widgets"

        # --model / --llm-provider-key drive the llm_models block.
        model = bundle["llm_models"][0]
        assert model["name"] == "claude-opus-4-1"
        assert model["provider_key"] == "anthropic"

        # --archetype pr-fixer changed the agent identity (default is engineer).
        assert "pr-fixer" in agent["name"]

    def test_default_archetype_is_engineer_when_flag_omitted(self, cli, tmp_path) -> None:
        out_file = tmp_path / "impl.json"
        result = cli(
            "scaffold",
            "implementation",
            "--prefix",
            "p",
            "--source",
            "github",
            "--owner",
            "o",
            "--repo",
            "r",
            "--out",
            str(out_file),
            env=_local_env(tmp_path),
        )
        assert result.code == 0, result.err
        bundle = json.loads(out_file.read_text())
        # Documented default archetype for `implementation` is `engineer`.
        assert bundle["agents"][0]["key"] == "p-engineer"

    def test_repeatable_source_collects_multiple_and_trigger_kind_applies(self, cli, tmp_path) -> None:
        out_file = tmp_path / "impl.json"
        result = cli(
            "scaffold",
            "implementation",
            "--prefix",
            "multi",
            "--source",
            "github",
            "--source",
            "jira",
            "--owner",
            "alice",
            "--repo",
            "widgets",
            "--trigger-kind",
            "schedule_cron",
            "--schedule",
            "30 2 * * *",
            "--out",
            str(out_file),
            env=_local_env(tmp_path),
        )
        assert result.code == 0, result.err
        bundle = json.loads(out_file.read_text())
        # Two repeated --source values both collected into the bundle.
        kinds = {s["kind"] for s in bundle["sources"]}
        assert kinds == {"github", "jira"}
        # --trigger-kind schedule_cron drives the cron trigger: it emits
        # kind "schedule" and carries the --schedule cron string verbatim.
        trigger = bundle["triggers"][0]
        assert trigger["kind"] == "schedule"
        assert trigger["schedule_cron"] == "30 2 * * *"
        assert trigger["key"] == "multi-cron"

    def test_missing_required_prefix_is_usage_error(self, cli, tmp_path) -> None:
        result = cli(
            "scaffold",
            "implementation",
            "--source",
            "github",
            "--owner",
            "o",
            "--repo",
            "r",
            env=_local_env(tmp_path),
        )
        # --prefix is REQUIRED -> argparse exits 2 with a stderr message.
        assert result.code == 2
        assert "prefix" in result.err

    def test_records_a_real_journal_session_to_disk(self, cli, tmp_path) -> None:
        """The composer is wrapped in a real journal `session(...)`. With
        the default journal installed (BRIAR_JOURNAL not off), a session
        file must be persisted recording the scaffold's decisions."""
        journal_root = tmp_path / "journal"
        env = _local_env(tmp_path)
        env.pop("BRIAR_JOURNAL")  # re-enable journaling for this test
        env["BRIAR_JOURNAL_ROOT"] = str(journal_root)

        result = cli(
            "scaffold",
            "implementation",
            "--prefix",
            "journaled",
            "--source",
            "github",
            "--owner",
            "alice",
            "--repo",
            "widgets",
            "--out",
            str(tmp_path / "out.json"),
            env=env,
        )
        assert result.code == 0, result.err

        session_files = list((journal_root / "sessions").rglob("*.json"))
        assert len(session_files) == 1, "scaffold did not persist exactly one journal session"
        session = json.loads(session_files[0].read_text())
        assert session["command"] == "scaffold.implementation"
        assert session["target"] == "journaled"
        assert session["closed"] is True
        choices = [d["choice"] for d in session["decisions"]]
        # The composer records each pluggable decision; these are stable slugs.
        assert "scaffold.archetype" in choices
        assert "scaffold.sources" in choices
        assert "scaffold.output" in choices


class TestScaffoldPrFixesReal:
    def test_pr_fixes_defaults_to_pr_fixer_one_shot(self, cli, tmp_path) -> None:
        out_file = tmp_path / "prfix.json"
        result = cli(
            "scaffold",
            "pr-fixes",
            "--prefix",
            "fixer",
            "--source",
            "github",
            "--owner",
            "alice",
            "--repo",
            "widgets",
            "--out",
            str(out_file),
            env=_local_env(tmp_path),
        )
        assert result.code == 0, result.err
        bundle = json.loads(out_file.read_text())
        # Documented defaults for pr-fixes: archetype=pr-fixer, shape=one-shot.
        assert bundle["agents"][0]["key"] == "fixer-pr-fixer"
        assert "one-shot" in bundle["workflows"][0]["description"]


# ════════════════════════════ secrets ═══════════════════════════════


class TestSecretsEnvfileStoreReal:
    def test_write_then_read_roundtrip_persists_and_never_echoes(self, cli, tmp_path, monkeypatch) -> None:
        """Drive the REAL EnvFileStore: write a placeholder secret, read it
        back, assert it landed on disk as `KEY=value`, and that the value
        is never printed."""
        from briar.credentials.envfile import EnvFileStore

        secrets_file = tmp_path / "secrets.env"
        monkeypatch.setenv("BRIAR_SECRETS_FILE", str(secrets_file))

        placeholder = "TOKEN-VALUE-PLACEHOLDER-not-a-secret"
        store = EnvFileStore()
        store.write("GITHUB_ACME_TOKEN", placeholder)

        # Persisted to the real file as a single KEY=value line.
        on_disk = secrets_file.read_text()
        assert on_disk == f"GITHUB_ACME_TOKEN={placeholder}\n"
        # 0600 — never group/world readable.
        assert oct(secrets_file.stat().st_mode & 0o777) == "0o600"
        # Read back returns exactly what we wrote.
        assert store.read("GITHUB_ACME_TOKEN") == placeholder

    def test_write_is_idempotent_replace_in_place(self, cli, tmp_path, monkeypatch) -> None:
        from briar.credentials.envfile import EnvFileStore

        secrets_file = tmp_path / "secrets.env"
        monkeypatch.setenv("BRIAR_SECRETS_FILE", str(secrets_file))
        store = EnvFileStore()
        store.write("GITHUB_ACME_TOKEN", "FIRST-PLACEHOLDER-not-a-secret")
        store.write("GITHUB_ACME_TOKEN", "SECOND-PLACEHOLDER-not-a-secret")
        # Replaced in place — exactly one line, the new value.
        assert secrets_file.read_text() == "GITHUB_ACME_TOKEN=SECOND-PLACEHOLDER-not-a-secret\n"
        assert store.read("GITHUB_ACME_TOKEN") == "SECOND-PLACEHOLDER-not-a-secret"

    def test_delete_removes_from_disk_and_env(self, cli, tmp_path, monkeypatch) -> None:
        from briar.credentials.envfile import EnvFileStore

        secrets_file = tmp_path / "secrets.env"
        monkeypatch.setenv("BRIAR_SECRETS_FILE", str(secrets_file))
        store = EnvFileStore()
        store.write("AWS_ACME_KEY", "KEY-PLACEHOLDER-not-a-secret")
        assert "AWS_ACME_KEY" in secrets_file.read_text()

        removed = store.delete("AWS_ACME_KEY")
        assert removed is True
        assert "AWS_ACME_KEY" not in secrets_file.read_text()
        assert store.read("AWS_ACME_KEY") is None

    def test_bootstrap_envfile_real_file_preserves_already_set_no_value_leak(self, cli, tmp_path, monkeypatch) -> None:
        """`briar secrets bootstrap --kind envfile --dry-run` against a REAL
        seeded secrets.env. The CLI's startup auto_bootstrap hydrates the
        keys into os.environ first, so the command's own bootstrap reports
        them as preserved (already-set) — assert that real behavior and
        that NO value ever reaches stdout."""
        secrets_file = tmp_path / "secrets.env"
        secrets_file.write_text("MYAPP_TOKEN=VALUE-PLACEHOLDER-not-a-secret\n" "MYAPP_OTHER=SECOND-PLACEHOLDER-not-a-secret\n")
        monkeypatch.setenv("BRIAR_SECRETS_FILE", str(secrets_file))

        result = cli(
            "secrets",
            "bootstrap",
            "--kind",
            "envfile",
            "--dry-run",
            env=_local_env(tmp_path),
        )
        assert result.code == 0, result.err
        # Startup hydrated both -> command sees them already-set.
        assert "would write 0 env vars (preserved 2 already-set)" in result.out
        # Values are NEVER echoed; key names may be, values must not.
        assert "VALUE-PLACEHOLDER-not-a-secret" not in result.out
        assert "SECOND-PLACEHOLDER-not-a-secret" not in result.out


# ════════════════════════════ telemetry ═════════════════════════════


class TestTelemetryRealConfigFile:
    def _state_path(self, tmp_path: Path) -> Path:
        return tmp_path / "xdg" / "briar" / "telemetry.json"

    def test_off_writes_off_tier_to_disk(self, cli, tmp_path) -> None:
        # `off` must explicitly be the chosen tier (not BRIAR_TELEMETRY=off
        # from the env helper, which the command overrides on disk).
        env = _local_env(tmp_path)
        env.pop("BRIAR_TELEMETRY")  # let the on-disk file be the source of truth
        result = cli("telemetry", "off", env=env)
        assert result.code == 0, result.err
        state = json.loads(self._state_path(tmp_path).read_text())
        assert state["tier"] == "off"

    def test_full_then_off_flips_tier_on_disk(self, cli, tmp_path) -> None:
        env = _local_env(tmp_path)
        env.pop("BRIAR_TELEMETRY")
        assert cli("telemetry", "full", env=env).code == 0
        assert json.loads(self._state_path(tmp_path).read_text())["tier"] == "full"
        assert cli("telemetry", "errors-only", env=env).code == 0
        assert json.loads(self._state_path(tmp_path).read_text())["tier"] == "errors-only"
        assert cli("telemetry", "off", env=env).code == 0
        assert json.loads(self._state_path(tmp_path).read_text())["tier"] == "off"

    def test_status_reads_the_persisted_tier(self, cli, tmp_path) -> None:
        env = _local_env(tmp_path)
        env.pop("BRIAR_TELEMETRY")
        cli("telemetry", "off", env=env)
        result = cli("--format", "json", "telemetry", "status", env=env)
        assert result.code == 0, result.err
        status = json.loads(result.out)
        assert status["tier"] == "off"
        assert status["source"] == "config-file"
        assert status["enabled"] is False
        # The reported state_path points inside our tmp XDG dir.
        assert str(tmp_path / "xdg") in status["state_path"]


# ════════════════════════════ context ═══════════════════════════════


class TestContextRealFileStore:
    def test_put_get_list_delete_roundtrip_on_disk(self, cli, tmp_path) -> None:
        root = tmp_path / "knowledge"
        env = _local_env(tmp_path)

        # put -> file written at <root>/<category>/<rest>.md
        put = cli(
            "--format",
            "json",
            "context",
            "--root",
            str(root),
            "put",
            "knowledge:acme",
            "--content",
            "# Acme\nhello world\n",
            env=env,
        )
        assert put.code == 0, put.err
        blob_path = root / "knowledge" / "acme.md"
        assert blob_path.read_text() == "# Acme\nhello world\n"
        meta = json.loads(put.out)
        assert meta["name"] == "knowledge:acme"
        assert meta["category"] == "knowledge"
        assert meta["byte_count"] == len("# Acme\nhello world\n")

        # get -> the markdown body to stdout
        got = cli("context", "--root", str(root), "get", "knowledge:acme", env=env)
        assert got.code == 0, got.err
        assert got.out == "# Acme\nhello world\n"

        # list -> the blob appears
        listed = cli("--format", "json", "context", "--root", str(root), "list", env=env)
        assert listed.code == 0, listed.err
        names = [r["name"] for r in json.loads(listed.out)]
        assert names == ["knowledge:acme"]

        # delete --yes -> file removed from disk
        deleted = cli("context", "--root", str(root), "delete", "knowledge:acme", "--yes", env=env)
        assert deleted.code == 0, deleted.err
        assert "deleted knowledge:acme" in deleted.out
        assert not blob_path.exists()

    def test_get_missing_blob_is_error_exit_1(self, cli, tmp_path) -> None:
        root = tmp_path / "knowledge"
        result = cli("context", "--root", str(root), "get", "knowledge:absent", env=_local_env(tmp_path))
        assert result.code == 1
        assert "blob not found" in result.err

    def test_from_file_source_writes_that_content(self, cli, tmp_path) -> None:
        root = tmp_path / "knowledge"
        src = tmp_path / "src.md"
        src.write_text("# From File\nbody\n")
        result = cli(
            "context",
            "--root",
            str(root),
            "put",
            "notes:topic",
            "--from-file",
            str(src),
            env=_local_env(tmp_path),
        )
        assert result.code == 0, result.err
        assert (root / "notes" / "topic.md").read_text() == "# From File\nbody\n"

    def test_list_prefix_filters_on_disk_blobs(self, cli, tmp_path) -> None:
        root = tmp_path / "knowledge"
        env = _local_env(tmp_path)
        cli("context", "--root", str(root), "put", "knowledge:acme", "--content", "a", env=env)
        cli("context", "--root", str(root), "put", "notes:misc", "--content", "b", env=env)
        listed = cli("--format", "json", "context", "--root", str(root), "list", "--prefix", "knowledge:", env=env)
        assert listed.code == 0, listed.err
        names = [r["name"] for r in json.loads(listed.out)]
        assert names == ["knowledge:acme"]  # `notes:misc` filtered out


# ════════════════════════════ journal ═══════════════════════════════


def _seed_journal(tmp_path: Path) -> tuple[Path, str]:
    """Write one real, closed session into a file JournalStore. Returns
    (journal_root, session_id)."""
    from briar.journal import make_journal_store
    from briar.journal.models import DecisionEvent, Session

    journal_root = tmp_path / "journal"
    store = make_journal_store("file", file_root=journal_root)
    session = Session(command="scaffold.implementation", target="acme/widgets")
    session.record(
        DecisionEvent(
            choice="scaffold.archetype",
            value="engineer",
            rationale="default agent role",
            alternatives=("engineer", "pr-fixer"),
        )
    )
    session.close()
    ref = store.put(session)
    return journal_root, ref.session_id


class TestJournalRealStore:
    def test_list_shows_seeded_session(self, cli, tmp_path) -> None:
        journal_root, session_id = _seed_journal(tmp_path)
        result = cli("journal", "list", "--root", str(journal_root), env=_local_env(tmp_path))
        assert result.code == 0, result.err
        assert session_id in result.out
        assert "scaffold.implementation" in result.out
        assert "target=acme/widgets" in result.out
        assert "decisions=1" in result.out

    def test_list_command_filter(self, cli, tmp_path) -> None:
        journal_root, _ = _seed_journal(tmp_path)
        env = _local_env(tmp_path)
        # Matching prefix -> shown.
        hit = cli("journal", "list", "--root", str(journal_root), "--command", "scaffold.", env=env)
        assert hit.code == 0
        assert "scaffold.implementation" in hit.out
        # Non-matching prefix -> empty.
        miss = cli("journal", "list", "--root", str(journal_root), "--command", "plan.", env=env)
        assert miss.code == 0
        assert "(no sessions)" in miss.out

    def test_show_renders_markdown(self, cli, tmp_path) -> None:
        journal_root, session_id = _seed_journal(tmp_path)
        result = cli("journal", "show", "--root", str(journal_root), session_id, env=_local_env(tmp_path))
        assert result.code == 0, result.err
        assert "# scaffold.implementation — acme/widgets" in result.out
        assert f"`{session_id}`" in result.out
        assert "### `scaffold.archetype`" in result.out
        assert "default agent role" in result.out

    def test_show_unknown_session_is_error(self, cli, tmp_path) -> None:
        journal_root, _ = _seed_journal(tmp_path)
        result = cli("journal", "show", "--root", str(journal_root), "deadbeef", env=_local_env(tmp_path))
        assert result.code == 1
        assert "not found" in result.err

    def test_export_json_to_file(self, cli, tmp_path) -> None:
        journal_root, session_id = _seed_journal(tmp_path)
        out_file = tmp_path / "exported.json"
        result = cli(
            "journal",
            "export",
            "--root",
            str(journal_root),
            session_id,
            "--as",
            "json",
            "--out",
            str(out_file),
            env=_local_env(tmp_path),
        )
        assert result.code == 0, result.err
        assert f"wrote {out_file}" in result.out
        payload = json.loads(out_file.read_text())
        assert payload["session_id"] == session_id
        assert payload["command"] == "scaffold.implementation"
        assert payload["decisions"][0]["choice"] == "scaffold.archetype"

    def test_export_markdown_to_stdout(self, cli, tmp_path) -> None:
        journal_root, session_id = _seed_journal(tmp_path)
        result = cli(
            "journal",
            "export",
            "--root",
            str(journal_root),
            session_id,
            "--as",
            "markdown",
            env=_local_env(tmp_path),
        )
        assert result.code == 0, result.err
        assert "# scaffold.implementation — acme/widgets" in result.out


# ════════════════════════════ dashboard ═════════════════════════════


class TestDashboardOnceReal:
    def test_once_renders_seeded_company_and_knowledge_into_html(self, cli, tmp_path) -> None:
        """`dashboard --once` runs the REAL collectors over a tmp sandbox
        and renders the HTML to stdout (no serve). Assert the seeded
        runbook company + knowledge blob appear in the page."""
        examples = tmp_path / "examples"
        examples.mkdir()
        (examples / "acme.yaml").write_text(
            "version: 1\n"
            "companies:\n"
            "  zzcorp:\n"
            "    knowledge:\n"
            "      store: file\n"
            "      name: knowledge:zzcorp\n"
            "    extract:\n"
            "      - name: pr-archaeology\n"
            "        args: {pr_repo: [zzcorp/api]}\n"
        )
        knowledge = tmp_path / "knowledge"
        from briar.storage import make_store

        store = make_store("file", file_root=knowledge)
        store.put("knowledge:zzcorp", "# zzcorp\n## PRs\nmerged PR sample: **3**\n", category="knowledge")

        result = cli(
            "dashboard",
            "--once",
            "--examples",
            str(examples),
            "--knowledge-store",
            "file",
            "--knowledge",
            str(knowledge),
            "--journal-store",
            "file",
            "--journal-root",
            str(tmp_path / "journal"),
            env=_local_env(tmp_path),
        )
        assert result.code == 0, result.err
        html = result.out
        assert "<html" in html.lower() or "<!doctype" in html.lower()
        # CompaniesCollector surfaced the seeded company + its extractor.
        assert "zzcorp" in html
        assert "pr-archaeology" in html
        # KnowledgeCollector surfaced the seeded blob name.
        assert "knowledge:zzcorp" in html


# ════════════════════════════ runbook ═══════════════════════════════


class TestRunbookExtractReal:
    def _write_runbook(self, tmp_path: Path) -> Path:
        """A real runbook whose only extractor is provider-backed
        (pr-archaeology). With GITHUB_* stripped by env_sandbox, the
        extractor reports unavailable and the executor records a
        deterministic 'empty (no sections)' row — no network."""
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir(exist_ok=True)
        rb = tmp_path / "acme.yaml"
        rb.write_text(
            "version: 1\n"
            "companies:\n"
            "  zzcorp:\n"
            "    knowledge:\n"
            "      store: file\n"
            "      name: knowledge:zzcorp\n"
            f"      root: {knowledge}\n"
            "    extract:\n"
            "      - name: pr-archaeology\n"
            "        args: {pr_repo: [zzcorp/api]}\n"
        )
        return rb

    def test_extract_runs_real_executor_records_row(self, cli, tmp_path) -> None:
        rb = self._write_runbook(tmp_path)
        result = cli("--format", "json", "runbook", "extract", str(rb), env=_local_env(tmp_path))
        assert result.code == 0, result.err
        rows = json.loads(result.out)
        assert len(rows) == 1
        row = rows[0]
        assert row["company"] == "zzcorp"
        assert row["task"] == "extractors"
        # No creds -> extractor unavailable -> no sections collected.
        assert row["status"] == "empty (no sections)"
        assert row["output"] == "knowledge:zzcorp"

    def test_extract_task_filter_no_match_skips(self, cli, tmp_path) -> None:
        rb = self._write_runbook(tmp_path)
        result = cli(
            "--format",
            "json",
            "runbook",
            "extract",
            str(rb),
            "--task",
            "nonexistent",
            env=_local_env(tmp_path),
        )
        assert result.code == 0, result.err
        # Filter matched nothing; with an explicit filter, no rows emitted.
        assert json.loads(result.out) == []

    def test_extract_missing_file_is_error(self, cli, tmp_path) -> None:
        missing = tmp_path / "does-not-exist.yaml"
        result = cli("runbook", "extract", str(missing), env=_local_env(tmp_path))
        assert result.code == 1
        assert "not found" in result.err

    def test_sweep_processes_each_yaml_in_dir(self, cli, tmp_path) -> None:
        self._write_runbook(tmp_path)  # writes acme.yaml + knowledge/
        # A second runbook so sweep has >1 file to walk.
        (tmp_path / "beta.yaml").write_text(
            "version: 1\n"
            "companies:\n"
            "  betacorp:\n"
            "    knowledge: {store: file, name: knowledge:betacorp, root: " + str(tmp_path / "knowledge") + "}\n"
            "    extract:\n"
            "      - name: pr-archaeology\n"
            "        args: {pr_repo: [betacorp/api]}\n"
        )
        result = cli("runbook", "sweep", str(tmp_path), env=_local_env(tmp_path))
        assert result.code == 0, result.err
        # Both files processed, in sorted order.
        assert "--- acme.yaml ---" in result.out
        assert "--- beta.yaml ---" in result.out
        assert result.out.index("--- acme.yaml ---") < result.out.index("--- beta.yaml ---")

    def test_sweep_not_a_directory_is_error(self, cli, tmp_path) -> None:
        not_dir = tmp_path / "f.txt"
        not_dir.write_text("x")
        result = cli("runbook", "sweep", str(not_dir), env=_local_env(tmp_path))
        assert result.code == 1
        assert "is not a directory" in result.err
