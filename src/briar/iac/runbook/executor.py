"""Runbook executor — walks `RunbookFile`, applies each runbook per
company with the right profile + credential context.

This is the bridge between the YAML schema (typed, validated) and the
existing scaffold composer (consumes argparse.Namespace + dict spec).
Per-company defaults inherit into each runbook before flattening.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml
from pydantic import ValidationError

from briar.credentials import CredentialsStore
from briar.errors import ConfigError
from briar.http import ApiClient
from briar.iac import TEMPLATES, ConfigFile
from briar.iac.engine import destroy_all, reconcile, summarise_ops
from briar.iac.runbook.models import (
    AwsSourceEntry,
    CompanyEntry,
    CronTriggerEntry,
    GithubSourceEntry,
    JiraSourceEntry,
    ManualTriggerEntry,
    RunbookEntry,
    RunbookFile,
    WebhookTriggerEntry,
)
from briar.profile import config_path_for


# Per-row report: (company, prefix, kind, name, op, uuid)
ApplyRow = Tuple[str, str, str, str, str, str]
DestroyRow = Tuple[str, str, str, str, str]


def load_runbook_file(path: Path) -> RunbookFile:
    """Parse YAML or JSON via Pydantic — same locator-aware errors as the
    main IaC config file. Auto-detects format from extension."""
    try:
        raw = path.read_text()
    except FileNotFoundError as exc:
        raise ConfigError(f"runbook not found: {path}") from exc

    suffix = path.suffix.lower()
    try:
        data = yaml.safe_load(raw) if suffix in {".yaml", ".yml"} else json.loads(raw)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{path}: invalid {suffix or 'JSON'} — {exc}") from exc

    if type(data) is not dict:
        raise ConfigError(f"{path}: top-level must be a mapping")

    try:
        return RunbookFile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            f"{path}: invalid runbook\n{_pretty_errors(exc)}"
        ) from exc


def _pretty_errors(exc: ValidationError) -> str:
    return "\n".join(
        f"  {'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
        for e in exc.errors()
    )


# ---------------------------------------------------------------------------
# Per-source unflatten — runbook YAML uses short keys per source kind;
# the composer expects flat `args.jira_project` / `args.aws_role_arn`
# names that match the argparse flags.
# ---------------------------------------------------------------------------

def _apply_github_source(spec: GithubSourceEntry, ns: argparse.Namespace) -> None:
    if spec.auth_mode:
        ns.auth_mode = spec.auth_mode
    if spec.github_secret_id:
        ns.github_secret_id = spec.github_secret_id
    # User filters — runbook YAML keys map 1:1 to the source template's
    # argparse `dest` names.
    for yaml_attr, ns_attr in (
        ("authors_allow",   "github_authors_allow"),
        ("authors_block",   "github_authors_block"),
        ("assignees_allow", "github_assignees_allow"),
        ("assignees_block", "github_assignees_block"),
    ):
        values = list(getattr(spec, yaml_attr) or [])
        if values:
            setattr(ns, ns_attr, values)


def _apply_jira_source(spec: JiraSourceEntry, ns: argparse.Namespace) -> None:
    if spec.project:
        ns.jira_project = list(spec.project)
    if spec.jql:
        ns.jira_jql = spec.jql
    if spec.secret_id:
        ns.jira_secret_id = spec.secret_id
    for yaml_attr, ns_attr in (
        ("authors_allow",   "jira_authors_allow"),
        ("authors_block",   "jira_authors_block"),
        ("assignees_allow", "jira_assignees_allow"),
        ("assignees_block", "jira_assignees_block"),
    ):
        values = list(getattr(spec, yaml_attr) or [])
        if values:
            setattr(ns, ns_attr, values)


def _apply_aws_source(spec: AwsSourceEntry, ns: argparse.Namespace) -> None:
    if spec.role_arn:
        ns.aws_role_arn = spec.role_arn
    if spec.external_id:
        ns.aws_external_id = spec.external_id
    if spec.region:
        ns.aws_region = spec.region
    if spec.services:
        ns.aws_services = list(spec.services)


_SOURCE_APPLIERS: Dict[str, Callable[[Any, argparse.Namespace], None]] = {
    "github": _apply_github_source,
    "jira":   _apply_jira_source,
    "aws":    _apply_aws_source,
}


def _apply_webhook_trigger(spec: WebhookTriggerEntry, ns: argparse.Namespace) -> None:
    ns.trigger_kind = "github_webhook"
    if spec.events:
        ns.webhook_events = list(spec.events)
    if spec.labels:
        ns.webhook_labels = list(spec.labels)


def _apply_cron_trigger(spec: CronTriggerEntry, ns: argparse.Namespace) -> None:
    ns.trigger_kind = "schedule_cron"
    ns.schedule = spec.schedule


def _apply_manual_trigger(spec: ManualTriggerEntry, ns: argparse.Namespace) -> None:
    ns.trigger_kind = "manual"


_TRIGGER_APPLIERS: Dict[str, Callable[[Any, argparse.Namespace], None]] = {
    "github_webhook": _apply_webhook_trigger,
    "schedule_cron":  _apply_cron_trigger,
    "manual":         _apply_manual_trigger,
}


# ---------------------------------------------------------------------------
# Build a fully-populated Namespace for the scaffold composer
# ---------------------------------------------------------------------------

_SCAFFOLD_DEFAULTS: Dict[str, Any] = {
    # Composer defaults; the runbook overrides what it wants.
    "archetype": "engineer",
    "shape": "plan-approve-act",
    "llm_provider_key": "anthropic",
    "model": "claude-sonnet-4-6",
    "auth_mode": "oauth",
    "github_secret_id": None,
    "jira_project": [],
    "jira_jql": None,
    "jira_secret_id": None,
    "aws_role_arn": None,
    "aws_external_id": None,
    "aws_region": "us-east-1",
    "aws_services": [],
    "webhook_events": [],
    "webhook_labels": ["briar"],
    "schedule": "0 * * * *",
    "trigger_kind": "schedule_cron",
}


def _build_namespace(
    runbook: RunbookEntry,
    defaults: Optional[Any],
) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in _SCAFFOLD_DEFAULTS.items():
        setattr(ns, k, list(v) if type(v) is list else v)
    # Per-runbook scaffolds that aren't `implementation` swap defaults —
    # honour them. The pr-fixes template prefers `pr-fixer` + `one-shot`.
    if runbook.template == "pr-fixes":
        ns.archetype = "pr-fixer"
        ns.shape = "one-shot"

    if defaults:
        for k in ("llm_provider_key", "model", "auth_mode",
                  "github_secret_id", "archetype", "shape"):
            value = getattr(defaults, k, None)
            if value is not None:
                setattr(ns, k, value)

    # Fixed-position fields straight off the runbook entry.
    ns.prefix = runbook.prefix
    ns.owner = runbook.owner
    ns.repo = runbook.repo
    ns.source = [s.kind for s in runbook.sources]

    # Per-runbook overrides.
    for attr in ("archetype", "shape", "llm_provider_key", "model"):
        v = getattr(runbook, attr)
        if v is not None:
            setattr(ns, attr, v)

    # Per-source kind-specific config (jira_project, aws_role_arn, …).
    for src in runbook.sources:
        applier = _SOURCE_APPLIERS.get(src.kind)
        if applier is None:
            raise ConfigError(f"no source-applier registered for {src.kind!r}")
        applier(src, ns)

    # Trigger config.
    trigger_applier = _TRIGGER_APPLIERS.get(runbook.trigger.kind)
    if trigger_applier is None:
        raise ConfigError(
            f"no trigger-applier registered for {runbook.trigger.kind!r}"
        )
    trigger_applier(runbook.trigger, ns)

    return ns


# ---------------------------------------------------------------------------
# Walk the runbook
# ---------------------------------------------------------------------------

def _client_for_company(company: CompanyEntry) -> ApiClient:
    store = CredentialsStore(config_path_for(company.profile), company.profile)
    if company.workspace_id:
        store.creds.workspace = company.workspace_id
    if company.api_base:
        store.creds.api_base = company.api_base
    return ApiClient(store)


def _runbook_to_bundle(runbook: RunbookEntry, defaults: Optional[Any]) -> ConfigFile:
    ns = _build_namespace(runbook, defaults)
    template = TEMPLATES.get(runbook.template)
    if template is None:
        raise ConfigError(f"unknown template {runbook.template!r}")
    bundle_dict = template.build(ns)
    return _config_from_dict(bundle_dict)


def _config_from_dict(data: Dict[str, Any]) -> ConfigFile:
    from briar.iac.models import ConfigSpec
    try:
        spec = ConfigSpec.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid generated config\n{_pretty_errors(exc)}") from exc
    return ConfigFile(spec)


def _resolve_binding(company: CompanyEntry):
    """Normalize legacy `knowledge_file:` into the same shape as the
    explicit `knowledge:` block. Returns None if neither is set."""
    if company.knowledge is not None:
        return company.knowledge
    if company.knowledge_file:
        from briar.iac.runbook.models import KnowledgeBinding
        return KnowledgeBinding(
            store="file", name=company.knowledge_file, mode="inject",
        )
    return None


def _open_store(binding, client: ApiClient):
    """Build the KnowledgeStore for this binding."""
    from briar.storage import make_store
    file_root = Path(binding.root) if (binding.store == "file" and binding.root) else None
    return make_store(binding.store, client=client, file_root=file_root)


def _read_knowledge(binding, client: ApiClient) -> str:
    """Fetch the blob's body from the chosen store, or a friendly
    placeholder if it's missing."""
    if binding is None:
        return ""
    store = _open_store(binding, client)
    body = store.get(binding.name)
    if body is None:
        return (
            f"\n_(knowledge blob {binding.name!r} not found in store "
            f"{binding.store!r} — run `briar runbook extract`)_\n"
        )
    return body


def _inject_knowledge(cfg: ConfigFile, knowledge: str) -> ConfigFile:
    """Prepend the knowledge blob to every agent's system_prompt."""
    if not knowledge:
        return cfg
    spec = cfg.spec.model_copy(deep=True)
    block = (
        "# Workspace knowledge base\n\n"
        f"{knowledge.strip()}\n\n---\n\n"
    )
    for agent in spec.agents:
        agent.system_prompt = block + (agent.system_prompt or "")
    return ConfigFile(spec)


def _bind_knowledge_source(cfg: ConfigFile, binding) -> ConfigFile:
    """`mode: bind` path — register the static blob as a workspace
    Source and add it to every agent's `source_keys`.

    The blob's content is assumed to already live in the workspace
    (uploaded via `briar runbook extract` or `briar context put`); this
    function only wires the existing source into the agent bindings."""
    spec = cfg.spec.model_copy(deep=True)
    source_key = f"knowledge-{binding.name.replace(':', '-')}"
    # Idempotent: add a placeholder source row in the bundle so the
    # reconciler's existing index_existing path picks up the live row
    # and substitutes its uuid into the agent's source_ids.
    spec.sources.append(_make_static_source_spec(source_key, binding.name))
    for agent in spec.agents:
        if source_key not in agent.source_keys:
            agent.source_keys = list(agent.source_keys) + [source_key]
    return ConfigFile(spec)


def _make_static_source_spec(key: str, blob_name: str):
    from briar.iac.models import SourceSpec
    return SourceSpec(
        key=key,
        name=blob_name,
        kind="static",
        config={},  # content already lives on the live row
    )


def apply_runbook(
    runbook_file: RunbookFile,
    *,
    dry_run: bool,
) -> List[ApplyRow]:
    rows: List[ApplyRow] = []
    for company_name, company in runbook_file.companies.items():
        client = _client_for_company(company)
        binding = _resolve_binding(company)

        for entry in company.runbooks:
            cfg = _runbook_to_bundle(entry, company.defaults)
            # The two modes are mutually exclusive; sequential `if`s
            # avoid `elif` per the project style and keep the dispatch
            # readable.
            if binding is not None and binding.mode == "inject":
                cfg = _inject_knowledge(cfg, _read_knowledge(binding, client))
            if binding is not None and binding.mode == "bind":
                cfg = _bind_knowledge_source(cfg, binding)
            for kind, name, op, uuid in reconcile(client, cfg, dry_run=dry_run):
                rows.append((company_name, entry.prefix, kind, name, op, uuid))
    return rows


def extract_runbook(
    runbook_file: RunbookFile,
) -> List[Tuple[str, str, str]]:
    """Walk every company's `extract:` list and write the result to its
    `knowledge_file`. Returns rows of (company, status, output_path).

    Lazy-imports the extract subpackage so a user who never runs
    `briar runbook extract` doesn't pay the boto3 import cost."""
    import argparse
    from briar.extract import EXTRACTORS
    from briar.extract.composer import render_markdown

    rows: List[Tuple[str, str, str]] = []
    for company_name, company in runbook_file.companies.items():
        if not company.extract:
            rows.append((company_name, "skipped (no extract section)", ""))
            continue

        binding = _resolve_binding(company) or _default_binding(company_name)
        client = _client_for_company(company)

        sections = []
        for entry in company.extract:
            extractor = EXTRACTORS.get(entry.name)
            if extractor is None:
                continue
            # Pre-seed defaults via the extractor's own argparse contract,
            # then overlay the runbook YAML args. This way required
            # defaults (pr_max=100, aws_extract_region=us-east-1, …)
            # exist even when the runbook omits them.
            seed = argparse.ArgumentParser(add_help=False)
            extractor.add_arguments(seed)
            ns = seed.parse_args([])
            for k, v in entry.args.items():
                setattr(ns, k, v)
            if not extractor.is_available(ns):
                continue
            section = extractor.extract(ns)
            if section is not None:
                sections.append(section)

        if not sections:
            rows.append((company_name, "empty (no sections)", binding.name))
            continue
        md = render_markdown(company=company_name, sections=sections)
        store = _open_store(binding, client)
        ref = store.put(binding.name, md, category="knowledge")
        rows.append((
            company_name,
            f"wrote {ref.byte_count} bytes via store={binding.store}",
            binding.name,
        ))
    return rows


def _default_binding(company_name: str):
    """When a company has `extract:` but no explicit `knowledge:` /
    `knowledge_file:`, write to `./knowledge/<company>.md`."""
    from briar.iac.runbook.models import KnowledgeBinding
    return KnowledgeBinding(
        store="file",
        name=f"./knowledge/{company_name}.md",
        mode="inject",
    )


def destroy_runbook(runbook_file: RunbookFile) -> List[DestroyRow]:
    rows: List[DestroyRow] = []
    for company_name, company in runbook_file.companies.items():
        client = _client_for_company(company)
        # Reverse order: destroy last-applied runbook first.
        for entry in reversed(company.runbooks):
            cfg = _runbook_to_bundle(entry, company.defaults)
            for kind, name, status in destroy_all(client, cfg):
                rows.append((company_name, entry.prefix, kind, name, status))
    return rows


def summarise_apply(rows: List[ApplyRow]) -> Dict[str, int]:
    # Reduce a flat list to the same shape `briar plan` already prints.
    return summarise_ops([(k, n, op, uid) for _, _, k, n, op, uid in rows])
