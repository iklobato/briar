"""`KnowledgeComposer.inventory` — stable structured-inventory JSON.

The point of `inventory()` over `json()` is byte-stability: no volatile
timestamp, sorted keys. That's what makes `put_if_changed` able to dedup
it, so the inventory blob's history only grows on real drift. These
tests pin that stability + that the full `data` payload survives."""

from __future__ import annotations

import json

from briar.extract.base import ExtractedSection
from briar.extract.composer import KnowledgeComposer


def _sections() -> list[ExtractedSection]:
    return [
        ExtractedSection(
            title="Resource inventory (2 tagged resource(s), 1 service(s))",
            body="- sqs: 2",
            data={"resources": [{"arn": "arn:aws:sqs:us-east-1:111:orders", "service": "sqs"}]},
        )
    ]


def test_inventory_is_byte_stable_across_calls() -> None:
    a = KnowledgeComposer.inventory(company="acme", sections=_sections())
    b = KnowledgeComposer.inventory(company="acme", sections=_sections())
    assert a == b


def test_inventory_omits_timestamp_unlike_json() -> None:
    inv = KnowledgeComposer.inventory(company="acme", sections=_sections())
    assert "generated_at" not in inv
    # json() (the human/programmatic variant) DOES stamp time — contrast.
    assert "generated_at" in KnowledgeComposer.json(company="acme", sections=_sections())


def test_inventory_preserves_full_data_payload() -> None:
    inv = KnowledgeComposer.inventory(company="acme", sections=_sections())
    parsed = json.loads(inv)
    assert parsed["company"] == "acme"
    section = parsed["sections"][0]
    # the full per-resource detail (dropped from markdown) is retained here
    assert section["data"]["resources"][0]["arn"] == "arn:aws:sqs:us-east-1:111:orders"


def test_inventory_keys_are_sorted() -> None:
    inv = KnowledgeComposer.inventory(company="acme", sections=_sections())
    # sort_keys=True → "company" precedes "sections" at the top level
    assert inv.index('"company"') < inv.index('"sections"')
