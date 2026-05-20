"""Reconciliation engine: plan / apply / destroy walks.

`reconcile()` plans or applies a `ConfigFile` against the live
workspace. `destroy_all()` walks the reverse order, recording per-row
status so one blocked delete (e.g. FK-protected resource) doesn't
abort the whole teardown."""

from __future__ import annotations

from typing import List, Tuple

from briar.errors import ApiError, CliError
from briar.http import ApiClient
from briar.iac.config_file import ConfigFile
from briar.iac.reconcilers import RECONCILER_ORDER
from briar.iac.reference_map import ReferenceMap


def reconcile(
    client: ApiClient,
    cfg: ConfigFile,
    *,
    dry_run: bool,
) -> List[Tuple[str, str, str, str]]:
    """Walk each section in dependency order.

    Returns rows of (kind, name, op, uuid) where `op` is one of
    `create` / `update` / `noop`."""
    refs = ReferenceMap(lenient=dry_run)
    # Pre-index every live resource so `*_key` references can target
    # admin-managed resources not declared in the config.
    for reconciler in RECONCILER_ORDER:
        reconciler.index_existing(client, refs)

    rows: List[Tuple[str, str, str, str]] = []
    for reconciler in RECONCILER_ORDER:
        for spec in cfg.section(reconciler.kind):
            rows.append(_reconcile_one(client, reconciler, spec, refs, dry_run))
    return rows


def _reconcile_one(
    client: ApiClient,
    reconciler,
    spec: dict,
    refs: ReferenceMap,
    dry_run: bool,
) -> Tuple[str, str, str, str]:
    name = reconciler.name_of(spec)
    existing = reconciler.find_existing(client, name)
    key = spec.get("key")

    # Pre-register so later reconcilers can resolve cross-refs in plan
    # mode (where this row hasn't actually been POSTed). Apply mode
    # overwrites the placeholder with the real uuid below.
    if key:
        placeholder = (
            existing.get("id", "")
            if existing
            else f"(planned:{reconciler.kind}.{key})"
        )
        refs.remember(reconciler.kind, key, placeholder)

    if dry_run:
        projected = reconciler.project(spec, refs)
        op = "create" if not existing else (
            "update"
            if any(existing.get(k) != v for k, v in projected.items())
            else "noop"
        )
        uuid = existing.get("id", "") if existing else "(would create)"
        return reconciler.kind, name, op, uuid

    op, uuid = reconciler.apply(client, spec, refs)
    if key:
        refs.remember(reconciler.kind, key, uuid)
    return reconciler.kind, name, op, uuid


def destroy_all(
    client: ApiClient,
    cfg: ConfigFile,
) -> List[Tuple[str, str, str]]:
    """Reverse dependency order; per-row error capture so a blocked
    delete records `blocked (HTTP X)` instead of aborting the loop."""
    rows: List[Tuple[str, str, str]] = []
    for reconciler in reversed(RECONCILER_ORDER):
        for spec in cfg.section(reconciler.kind):
            name = reconciler.name_of(spec)
            try:
                removed = reconciler.destroy(client, spec)
                rows.append(
                    (reconciler.kind, name,
                     "deleted" if removed else "not found")
                )
            except ApiError as exc:
                rows.append(
                    (reconciler.kind, name, f"blocked (HTTP {exc.status})")
                )
            except CliError as exc:
                rows.append((reconciler.kind, name, f"error: {exc}"))
    return rows


def summarise_ops(
    rows: List[Tuple[str, str, str, str]],
) -> dict:
    out = {"create": 0, "update": 0, "noop": 0}
    for _, _, op, _ in rows:
        out[op] = out.get(op, 0) + 1
    return out
