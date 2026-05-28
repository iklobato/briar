"""`briar secrets` — credential management surface.

Subcommands:
  doctor      walk the configured runbooks + report which (company, extractor,
              env var) tuples are set vs missing — without ever printing values.
  bootstrap   one-off invocation of a credential bootstrap (e.g. Infisical
              fetch) into the running process's env.

The doctor reads required env-var lists FROM THE PROVIDER CLASSES
(``RepositoryProvider.required_env_vars`` / etc.) rather than from a
hand-maintained ``(extractor × provider) → creds`` table. Adding a new
extractor or provider needs no edit here — the provider declares its
requirements and the doctor picks them up via
``KnowledgeExtractor.provider_class_for(args)``."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import ClassVar, Dict

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

        from briar.credentials._bootstraps import CredentialBootstrapRegistry

        bootstrap = sub.add_parser(
            "bootstrap",
            help="One-off invocation of a credential bootstrap (e.g. Infisical fetch). "
            "Normally runs automatically at CLI startup; this subcommand is for testing.",
        )
        bootstrap.add_argument(
            "--kind",
            default="",
            choices=list(CredentialBootstrapRegistry.kinds()),
            help="Force one bootstrap backend. Default: auto-detect via is_available().",
        )
        bootstrap.add_argument(
            "--dry-run",
            action="store_true",
            help="Run the remote fetch but DON'T write to os.environ. Prints the keys that "
            "would be set, without revealing values.",
        )

    _ACTIONS: ClassVar[Dict[str, str]] = {
        "doctor": "_doctor",
        "bootstrap": "_bootstrap",
    }

    def run(self, args: argparse.Namespace) -> int:
        handler_name = self._ACTIONS.get(args.secrets_action)
        if handler_name is None:
            known = ", ".join(sorted(self._ACTIONS.keys()))
            print(f"unknown secrets action: {args.secrets_action} (known: {known})")
            return 1
        return getattr(self, handler_name)(args)

    @staticmethod
    def _bootstrap(args: argparse.Namespace) -> int:
        from briar.credentials._bootstraps import auto_bootstrap, make_bootstrap

        if args.kind:
            bs = make_bootstrap(args.kind)
            if not bs.is_available():
                names = ", ".join(bs.required_env_vars())
                print(f"bootstrap kind={bs.kind} not configured — required env: {names}")
                return 1
            results = [bs.hydrate(dry_run=args.dry_run)]
        else:
            results = auto_bootstrap(dry_run=args.dry_run)

        if not results:
            print("no credential-bootstrap backend configured (auto-detect found nothing)")
            return 0

        marker = "would write" if args.dry_run else "wrote"
        any_failed = False
        for result in results:
            if not result.ok:
                any_failed = True
                print(f"bootstrap {result.backend} failed: {result.error}")
                continue
            print(f"bootstrap {result.backend}: {marker} {result.count} env vars (preserved {len(result.skipped)} already-set)")
            if result.written:
                print("  keys: " + ", ".join(sorted(result.written)))
        # Cascade exit code: any successful backend → 0 (operator can proceed
        # with what we did hydrate); all-failed → 1.
        return 1 if any_failed and not any(r.ok for r in results) else 0

    def _doctor(self, args: argparse.Namespace) -> int:
        from briar.extract import EXTRACTORS
        from briar.iac.runbook import load_runbook_file
        from briar.iac.runbook.executor import RunbookSchedules
        from briar.messaging import WRITERS

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

                # Audit the `messages:` block too — each writer has its
                # own required_env_vars classmethod, same shape as the
                # provider audit above.
                for handle, binding in (getattr(company, "messages", {}) or {}).items():
                    kind = getattr(binding, "kind", "") or (binding.get("kind", "") if isinstance(binding, dict) else "")
                    writer_cls = WRITERS.get(kind)
                    if writer_cls is None:
                        print(f"  ?  messages.{handle} (kind={kind}) — unknown writer, skipping")
                        continue
                    required = writer_cls.required_env_vars(company=company_name)
                    missing = [env_name for env_name in required if not store.read(env_name)]
                    if missing:
                        any_missing = True
                        print(f"  X  messages.{handle} (kind={kind}) — MISSING: {', '.join(missing)}")
                    else:
                        print(f"  ok messages.{handle} (kind={kind})")
        return 1 if any_missing else 0
