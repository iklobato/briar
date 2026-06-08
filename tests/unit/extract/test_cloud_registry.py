"""Cloud registry + the base CloudProvider generic subsection walker.

Covers:
- CloudRegistry.make dispatch + unknown-kind CliError.
- The base-class list_subsections() generic walker (used by Azure/GCP
  which don't override it) — asserts exact section titles, bodies and
  data payloads it builds from the four list_* verbs, and that empty
  verbs produce NO section.
- caller_identity / required_env_vars defaults on the ABC.
"""

from __future__ import annotations

from typing import List

import pytest

from briar.errors import CliError
from briar.extract._cloud import AccountIdentity, CloudProvider, ComputeResource, DatabaseResource, LogGroup, QueueResource
from briar.extract._clouds import CLOUDS, CloudRegistry, make_cloud

pytestmark = pytest.mark.registry


# ─── registry dispatch ───────────────────────────────────────────────


def test_kinds_lists_all_three_providers() -> None:
    assert set(CloudRegistry.kinds()) == {"aws", "gcp", "azure"}
    assert set(CLOUDS.keys()) == {"aws", "gcp", "azure"}


def test_unknown_provider_raises_cli_error() -> None:
    with pytest.raises(CliError) as ctx:
        make_cloud("digitalocean", company="acme")
    msg = str(ctx.value)
    assert "digitalocean" in msg
    # Error lists the known kinds sorted.
    assert "aws" in msg and "azure" in msg and "gcp" in msg


# ─── base-class generic subsection walker ────────────────────────────


class _FakeCloud(CloudProvider):
    """Minimal CloudProvider that does NOT override list_subsections, so
    the base-class generic walker is exercised."""

    kind = "fake"

    def __init__(self, *, compute=None, dbs=None, queues=None, logs=None) -> None:
        self._compute = compute or []
        self._dbs = dbs or []
        self._queues = queues or []
        self._logs = logs or []

    def is_available(self) -> bool:
        return True

    def caller_identity(self) -> AccountIdentity:
        return AccountIdentity(account_id="acct-1", region="r1")

    def list_compute(self) -> List[ComputeResource]:
        return self._compute

    def list_databases(self) -> List[DatabaseResource]:
        return self._dbs

    def list_queues(self) -> List[QueueResource]:
        return self._queues

    def list_log_groups(self, *, top_by_bytes: int = 10) -> List[LogGroup]:
        return self._logs


def test_generic_walker_builds_all_four_sections() -> None:
    cloud = _FakeCloud(
        compute=[ComputeResource(name="web", kind="cloud-run", region="r1")],
        dbs=[DatabaseResource(name="db1", engine="postgres", version="15", instance_class="big", region="r1", multi_az=True)],
        queues=[QueueResource(name="orders", kind="pubsub-topic", region="r1")],
        logs=[LogGroup(name="lg1", stored_bytes=2048, retention_days=30)],
    )

    sections = cloud.list_subsections()

    by_title = {s.title: s for s in sections}
    assert set(by_title) == {"Compute", "Databases", "Queues", "Log groups (top 10 by size)"}

    assert by_title["Compute"].body == "- web (cloud-run, r1)"
    assert by_title["Compute"].data == {"resources": [{"name": "web", "kind": "cloud-run", "region": "r1"}]}

    assert by_title["Databases"].body == "- db1 postgres 15 (big)"
    assert by_title["Databases"].data == {"instances": [{"identifier": "db1", "engine": "postgres", "version": "15", "class": "big", "multi_az": True}]}

    assert by_title["Queues"].body == "- orders (pubsub-topic)"
    assert by_title["Queues"].data == {"queues": [{"name": "orders", "kind": "pubsub-topic"}]}

    assert by_title["Log groups (top 10 by size)"].body == "- lg1 (2048 bytes, retention=30d)"
    assert by_title["Log groups (top 10 by size)"].data == {"groups": [{"name": "lg1", "stored_bytes": 2048, "retention_days": 30}]}


def test_generic_walker_omits_empty_verbs() -> None:
    # Only compute populated → exactly one section, no empty Databases/etc.
    cloud = _FakeCloud(compute=[ComputeResource(name="web", kind="aci", region="r1")])
    sections = cloud.list_subsections()
    assert [s.title for s in sections] == ["Compute"]


def test_generic_walker_empty_when_nothing() -> None:
    assert _FakeCloud().list_subsections() == []


# ─── ABC defaults ────────────────────────────────────────────────────


def test_default_list_verbs_return_empty() -> None:
    """The base CloudProvider's list_* verbs default to [] so a provider
    that only implements caller_identity still works."""

    class Minimal(CloudProvider):
        kind = "minimal"

        def is_available(self) -> bool:
            return True

        def caller_identity(self) -> AccountIdentity:
            return AccountIdentity(account_id="a", region="r")

    m = Minimal()
    assert m.list_compute() == []
    assert m.list_databases() == []
    assert m.list_queues() == []
    assert m.list_log_groups() == []
    assert m.list_subsections() == []


def test_required_env_vars_default_empty() -> None:
    assert CloudProvider.required_env_vars() == []
    assert CloudProvider.required_env_vars("acme") == []
