"""`briar secrets` — credential management surface.

Subcommands:
  doctor   walk the configured runbooks + report which (company, extractor,
           env var) tuples are set vs missing — without ever printing values.

The doctor reads required env-var lists FROM THE PROVIDER CLASSES
(``RepositoryProvider.required_env_vars`` / etc.) rather than from a
hand-maintained ``(extractor × provider) → creds`` table. Adding a new
extractor or provider needs no edit here — the provider declares its
requirements and the doctor picks them up via
``KnowledgeExtractor.provider_class_for(args)``."""

from __future__ import annotations

import argparse
from pathlib import Path

from briar.commands.base import Command
from briar.credentials import CredentialStoreRegistry, make_credential_store


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
        from briar.extract import EXTRACTORS
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
                        extractor = EXTRACTORS.get(entry.name)
                        if extractor is None:
                            print(f"  ?  {entry.name} — unknown extractor, skipping")
                            continue
                        # Synthesize the same Namespace the runbook
                        # executor would build for this entry.
                        ns = argparse.Namespace(company=company_name, **entry.args)
                        provider_cls = extractor.provider_class_for(ns)
                        if provider_cls is None:
                            # Local-only / not provider-backed (no
                            # credential dependency). Report as ok.
                            print(f"  ok {entry.name} (no provider deps)")
                            continue
                        provider_kind = getattr(provider_cls, "kind", "?")
                        required = provider_cls.required_env_vars(company=company_name)
                        missing = [env_name for env_name in required if not store.read(env_name)]
                        if missing:
                            any_missing = True
                            print(f"  X  {entry.name} (provider={provider_kind}) — MISSING: {', '.join(missing)}")
                        else:
                            print(f"  ok {entry.name} (provider={provider_kind})")
        return 1 if any_missing else 0
