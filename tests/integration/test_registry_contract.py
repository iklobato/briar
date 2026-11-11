"""Parametrized contract over every plugin registry.

Replaces ~6 near-identical per-registry test classes. Catches: typos in
class `name`/`kind` ClassVar, duplicate registrations (build_registry
guards), missing required ABC methods (ABC instantiation raises)."""

from __future__ import annotations

import pytest

from briar.extract import EXTRACTORS
from briar.formatting import FORMATTERS


# (registry, name_attribute) — passed in so the test knows which attr to read.
_REGISTRIES = [
    pytest.param(EXTRACTORS, "name", id="EXTRACTORS"),
    pytest.param(FORMATTERS, "name", id="FORMATTERS"),
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
        (FORMATTERS, "name"),
    ])
    def test_no_duplicate_names(self, registry, name_attr) -> None:
        keys = _kinds(registry)
        assert len(keys) == len(set(keys)), f"duplicate names in {registry}: {keys}"

    @pytest.mark.parametrize("registry,name_attr", [
        (EXTRACTORS, "name"),
        (FORMATTERS, "name"),
    ])
    def test_every_name_nonempty(self, registry, name_attr) -> None:
        keys = _kinds(registry)
        assert keys, f"{registry} is empty"
        assert all(k for k in keys), f"empty name in {registry}: {keys}"

    @pytest.mark.parametrize("registry,name_attr", [
        (EXTRACTORS, "name"),
        (FORMATTERS, "name"),
    ])
    def test_each_entry_value_class_attr_matches_key(self, registry, name_attr) -> None:
        for key, value in registry.items():
            # `value` may be a class or an instance; either way, class attr
            # should match the key.
            attr_value = getattr(value, name_attr, None) or getattr(type(value), name_attr, None)
            assert attr_value == key, f"{registry}: key {key!r} does not match {name_attr}={attr_value!r}"


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
