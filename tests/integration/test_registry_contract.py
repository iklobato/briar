"""Parametrized contract over every plugin registry.

Replaces ~6 near-identical per-registry test classes. Catches: typos in
class `name`/`kind` ClassVar, duplicate registrations (build_registry
guards), missing required ABC methods (ABC instantiation raises)."""

from __future__ import annotations

import pytest

from briar.auth import AcquirerRegistry
from briar.credentials import STORES
from briar.credentials._bootstraps import CredentialBootstrapRegistry
from briar.extract import EXTRACTORS, TASK_SCOPED_EXTRACTORS
from briar.formatting import FORMATTERS
from briar.iac.scaffold.archetypes import ARCHETYPES
from briar.journal.sinks import JOURNAL_SINKS
from briar.messaging import WRITERS
from briar.notify import SINKS
from briar.plan._boards import BOARD_READERS


# (registry, name_attribute) — passed in so the test knows which attr to read.
_REGISTRIES = [
    pytest.param(EXTRACTORS, "name", id="EXTRACTORS"),
    pytest.param(TASK_SCOPED_EXTRACTORS, "name", id="TASK_SCOPED_EXTRACTORS"),
    pytest.param(STORES, "kind", id="STORES"),
    pytest.param(ACQUIRERS_AS_REGISTRY := AcquirerRegistry, "kind", id="ACQUIRERS"),
    pytest.param(WRITERS, "kind", id="WRITERS"),
    pytest.param(SINKS, "kind", id="SINKS"),
    pytest.param(BOARD_READERS, "kind", id="BOARD_READERS"),
    pytest.param(FORMATTERS, "name", id="FORMATTERS"),
    pytest.param(JOURNAL_SINKS, "name", id="JOURNAL_SINKS"),
    pytest.param(ARCHETYPES, "name", id="ARCHETYPES"),
]


def _kinds(registry) -> list[str]:
    """Uniform `.keys()` accessor across dict registries + Registry classes."""
    if hasattr(registry, "kinds") and callable(registry.kinds):
        return list(registry.kinds())
    if hasattr(registry, "keys"):
        return list(registry.keys())
    return []


def _items(registry) -> list:
    """Uniform `.values()` accessor."""
    if isinstance(registry, dict):
        return list(registry.values())
    # Try class-based registries
    if hasattr(registry, "kinds"):
        kinds = list(registry.kinds())
        if hasattr(registry, "make"):
            return [registry.make(k) for k in kinds]
    return []


@pytest.mark.registry
class TestRegistryContract:
    @pytest.mark.parametrize("registry,name_attr", [
        (EXTRACTORS, "name"),
        (TASK_SCOPED_EXTRACTORS, "name"),
        (STORES, "kind"),
        (WRITERS, "kind"),
        (SINKS, "kind"),
        (BOARD_READERS, "kind"),
        (FORMATTERS, "name"),
        (JOURNAL_SINKS, "name"),
        (ARCHETYPES, "name"),
    ])
    def test_no_duplicate_names(self, registry, name_attr) -> None:
        keys = _kinds(registry)
        assert len(keys) == len(set(keys)), f"duplicate names in {registry}: {keys}"

    @pytest.mark.parametrize("registry,name_attr", [
        (EXTRACTORS, "name"),
        (STORES, "kind"),
        (WRITERS, "kind"),
        (SINKS, "kind"),
        (BOARD_READERS, "kind"),
        (FORMATTERS, "name"),
        (ARCHETYPES, "name"),
    ])
    def test_every_name_nonempty(self, registry, name_attr) -> None:
        keys = _kinds(registry)
        assert keys, f"{registry} is empty"
        assert all(k for k in keys), f"empty name in {registry}: {keys}"

    @pytest.mark.parametrize("registry,name_attr", [
        (EXTRACTORS, "name"),
        (STORES, "kind"),
        (WRITERS, "kind"),
        (SINKS, "kind"),
        (FORMATTERS, "name"),
        (JOURNAL_SINKS, "name"),
    ])
    def test_each_entry_value_class_attr_matches_key(self, registry, name_attr) -> None:
        for key, value in registry.items():
            # `value` may be a class or an instance; either way, class attr
            # should match the key.
            attr_value = getattr(value, name_attr, None) or getattr(type(value), name_attr, None)
            assert attr_value == key, f"{registry}: key {key!r} does not match {name_attr}={attr_value!r}"


class TestAcquirerRegistry:
    def test_kinds_nonempty(self) -> None:
        assert len(AcquirerRegistry.kinds()) > 0

    @pytest.mark.parametrize("kind", AcquirerRegistry.kinds())
    def test_factory_returns_instance(self, kind: str) -> None:
        acquirer = AcquirerRegistry.make(kind)
        assert acquirer is not None
        # `kind` class attribute must be set
        assert type(acquirer).kind == kind


class TestCredentialStoreRegistry:
    @pytest.mark.parametrize("kind", list(STORES.keys()))
    def test_factory_instantiates_each_store(self, kind: str) -> None:
        # Most stores construct fine even without creds (they fail at read/write).
        cls = STORES[kind]
        instance = cls()  # type: ignore[call-arg]
        assert instance is not None


class TestExtractorRegistry:
    @pytest.mark.parametrize("name", list(EXTRACTORS.keys()))
    def test_extractor_is_available_returns_bool(self, name: str) -> None:
        import argparse

        ext = EXTRACTORS[name]
        # Build a parser the extractor would register flags into — gives
        # us the defaults it expects without needing to know them.
        parser = argparse.ArgumentParser()
        parser.add_argument("--company", default="acme")
        try:
            ext.add_arguments(parser)
        except argparse.ArgumentError:
            # Extractors share some flags; duplicates already guarded
            # inside their add_arguments, but our test parser doesn't.
            pass
        ns = parser.parse_args([])
        result = ext.is_available(ns)
        assert isinstance(result, bool)


class TestWriterRegistry:
    @pytest.mark.parametrize("kind", list(WRITERS.keys()))
    def test_writer_is_available_returns_bool(self, kind: str) -> None:
        cls = WRITERS[kind]
        writer = cls(company="acme", config={})
        assert isinstance(writer.is_available(), bool)

    @pytest.mark.parametrize("kind", list(WRITERS.keys()))
    def test_writer_required_env_vars_returns_list_of_str(self, kind: str) -> None:
        cls = WRITERS[kind]
        names = cls.required_env_vars(company="acme")
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)


class TestSinkRegistry:
    @pytest.mark.parametrize("kind", list(SINKS.keys()))
    def test_sink_is_available_returns_bool(self, kind: str) -> None:
        cls = SINKS[kind]
        sink = cls(company="acme")
        assert isinstance(sink.is_available(), bool)


class TestFormatterMatrix:
    """Every formatter must accept every payload shape without raising."""

    @pytest.mark.parametrize("payload", [
        [],
        [{"id": "a", "name": "x"}],
        {"results": [{"id": "a"}]},
        {"id": "single"},
        None,
        "string",
        42,
    ])
    @pytest.mark.parametrize("fmt", list(FORMATTERS.keys()))
    def test_render_does_not_raise(self, payload, fmt, capsys) -> None:
        from briar.formatting import render

        render(payload, fmt)
        # render writes to stdout — capsys absorbs it; we only care that
        # no exception bubbles out.
