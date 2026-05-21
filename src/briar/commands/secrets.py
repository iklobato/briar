"""`briar secrets` — credential management surface.

Subcommands:
  doctor   walk the configured runbooks + report which (company, extractor,
           env var) tuples are set vs missing — without ever printing values.

The doctor is the real consumer for the `CredentialStore` abstraction.
Today it always uses the EnvFile backend; once you migrate to AWS Secrets
Manager / Vault, swap one flag and every doctor call resolves via the
new backend with no code change."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Tuple

from briar.commands.base import Command
from briar.credentials import CredentialStoreRegistry, make_credential_store
from briar.env_vars import CredEnv


# What each extractor + its `--provider` value implies in terms of
# required env vars. Kept here (not on the extractor) because the
# doctor needs a *static* answer — it can't import-and-run extractors
# to discover their auth needs at scan time.
_EXTRACTOR_REQUIREMENTS: Dict[Tuple[str, str], List[CredEnv]] = {
    ("pr-archaeology", "github"): [CredEnv.GITHUB_TOKEN],
    ("pr-archaeology", "bitbucket"): [CredEnv.BITBUCKET_USERNAME, CredEnv.BITBUCKET_APP_PASSWORD, CredEnv.BITBUCKET_WORKSPACE],
    ("active-work", "github"): [CredEnv.GITHUB_TOKEN],
    ("active-work", "bitbucket"): [CredEnv.BITBUCKET_USERNAME, CredEnv.BITBUCKET_APP_PASSWORD, CredEnv.BITBUCKET_WORKSPACE],
    ("github-deployments", "github"): [CredEnv.GITHUB_TOKEN],
    ("github-deployments", "bitbucket"): [CredEnv.BITBUCKET_USERNAME, CredEnv.BITBUCKET_APP_PASSWORD, CredEnv.BITBUCKET_WORKSPACE],
    ("codebase-conventions", "github"): [CredEnv.GITHUB_TOKEN],
    ("codebase-conventions", "bitbucket"): [CredEnv.BITBUCKET_USERNAME, CredEnv.BITBUCKET_APP_PASSWORD, CredEnv.BITBUCKET_WORKSPACE],
    ("active-tickets", "jira"): [CredEnv.JIRA_URL, CredEnv.JIRA_EMAIL, CredEnv.JIRA_TOKEN],
    ("active-tickets", "github-issues"): [CredEnv.GITHUB_TOKEN],
    ("active-tickets", "bitbucket-issues"): [CredEnv.BITBUCKET_USERNAME, CredEnv.BITBUCKET_APP_PASSWORD, CredEnv.BITBUCKET_WORKSPACE],
    ("active-tickets", "linear"): [CredEnv.LINEAR_TOKEN],
    ("ticket-archaeology", "jira"): [CredEnv.JIRA_URL, CredEnv.JIRA_EMAIL, CredEnv.JIRA_TOKEN],
    ("ticket-archaeology", "github-issues"): [CredEnv.GITHUB_TOKEN],
    ("ticket-archaeology", "bitbucket-issues"): [CredEnv.BITBUCKET_USERNAME, CredEnv.BITBUCKET_APP_PASSWORD, CredEnv.BITBUCKET_WORKSPACE],
    ("ticket-archaeology", "linear"): [CredEnv.LINEAR_TOKEN],
    ("aws-infra", "aws"): [CredEnv.AWS_KEY_ID, CredEnv.AWS_SECRET],
}


class CommandSecrets(Command):
    name = "secrets"
    help = "Inspect credential coverage (briar secrets doctor)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="secrets_action", required=True)

        doctor = sub.add_parser("doctor", help="Audit (company, extractor) → env-var coverage")
        doctor.add_argument(
            "--examples",
            type=Path,
            default=Path("./examples"),
            help="Runbook YAML directory (default: ./examples)",
        )
        doctor.add_argument(
            "--store",
            default="envfile",
            choices=list(CredentialStoreRegistry.kinds()),
            help="Credential store backend (default: envfile)",
        )

    def run(self, args: argparse.Namespace) -> int:
        if args.secrets_action == "doctor":
            return self._doctor(args)
        return 1

    def _doctor(self, args: argparse.Namespace) -> int:
        from briar.iac.runbook import load_runbook_file
        from briar.iac.runbook.executor import RunbookSchedules

        store = make_credential_store(args.store)
        examples_dir: Path = args.examples
        if not examples_dir.exists():
            print(f"no examples dir at {examples_dir}")
            return 1

        any_missing = False
        for yaml_path in sorted(examples_dir.glob("*.yaml")):
            try:
                rb = load_runbook_file(yaml_path)
            except Exception as exc:  # noqa: BLE001
                print(f"  {yaml_path.name}: load failed — {exc}")
                continue
            for company_name, company in rb.companies.items():
                print(f"\n=== {company_name} ({yaml_path.name}) ===")
                for schedule in RunbookSchedules.for_company(company):
                    for entry in schedule.extract:
                        provider_kind = entry.args.get("provider") or entry.args.get("tracker") or entry.args.get("cloud") or self._default_provider_for(entry.name)
                        key = (entry.name, provider_kind)
                        creds = _EXTRACTOR_REQUIREMENTS.get(key)
                        if creds is None:
                            print(f"  ?  {entry.name} (provider={provider_kind}) — no requirements registered, skipping")
                            continue
                        missing = []
                        for cred in creds:
                            env_name = cred.for_company(company_name)
                            if not store.read(env_name):
                                missing.append(env_name)
                        if missing:
                            any_missing = True
                            print(f"  X  {entry.name} (provider={provider_kind}) — MISSING: {', '.join(missing)}")
                        else:
                            print(f"  ok {entry.name} (provider={provider_kind})")
        return 1 if any_missing else 0

    @staticmethod
    def _default_provider_for(extractor_name: str) -> str:
        # Mirrors the defaults declared on RepoBackedExtractor / TrackerBackedExtractor /
        # CloudBackedExtractor. Keep in sync if those change.
        if extractor_name in ("active-tickets", "ticket-archaeology"):
            return "jira"
        if extractor_name == "aws-infra":
            return "aws"
        return "github"
