"""`HclParser`: the small reader of Terraform files that `briar extract` uses (no HCL library is a dependency).

It reads blocks, their labels, their top-level attributes and their child blocks, and it must not be
fooled by a brace inside a string, a comment or a heredoc."""

from __future__ import annotations

from typing import List

import pytest

from briar.extract._hcl import HclBlock, HclParser

MAIN_TF = """
# A comment with a { brace
terraform {
  backend "azurerm" {
    storage_account_name = "stgprod"
    container_name       = "tfstate"
    key                  = "prod.tfstate"
  }
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 3.0" }
  }
}

provider "azurerm" {
  features {}
}

module "network" {
  source  = "git::https://host.example/mods.git//network?ref=v1.2.0"
  version = "1.2.0"
  name    = "has a } brace in a string"
}

resource "azurerm_thing" "x" {
  policy = <<EOT
{ "not": "a block" }
EOT
}
"""

TERRAFORM, PROVIDER, MODULE, RESOURCE = 0, 1, 2, 3
STARTS_AT_LINES = (3, 14, 18, 24)
SECOND_CHILD = 1


@pytest.fixture
def blocks() -> List[HclBlock]:
    return HclParser().parse(MAIN_TF)


class TestBlocks:
    def test_the_top_level_blocks_come_in_order_with_their_labels(self, blocks: List[HclBlock]) -> None:
        assert [(block.kind, block.labels) for block in blocks] == [
            ("terraform", ()),
            ("provider", ("azurerm",)),
            ("module", ("network",)),
            ("resource", ("azurerm_thing", "x")),
        ]

    def test_a_block_keeps_its_child_blocks(self, blocks: List[HclBlock]) -> None:
        backend = blocks[TERRAFORM].children[0]

        assert (backend.kind, backend.labels) == ("backend", ("azurerm",))

    def test_attributes_are_the_top_level_key_values_without_quotes(self, blocks: List[HclBlock]) -> None:
        assert blocks[TERRAFORM].children[0].attributes == {"storage_account_name": "stgprod", "container_name": "tfstate", "key": "prod.tfstate"}

    def test_a_brace_in_a_string_does_not_end_the_block(self, blocks: List[HclBlock]) -> None:
        module = blocks[MODULE]

        assert module.attributes["name"] == "has a } brace in a string"
        assert module.attributes["version"] == "1.2.0"

    def test_a_heredoc_does_not_start_a_block_and_does_not_end_the_resource(self, blocks: List[HclBlock]) -> None:
        assert blocks[RESOURCE].children == ()

    def test_a_map_value_is_reported_as_complex_not_parsed(self, blocks: List[HclBlock]) -> None:
        required = blocks[TERRAFORM].children[SECOND_CHILD]

        assert required.attributes == {"azurerm": "(complex)"}

    def test_the_line_of_a_block_is_where_it_starts(self, blocks: List[HclBlock]) -> None:
        assert tuple(block.line for block in blocks) == STARTS_AT_LINES


class TestAttributes:
    def test_a_tfvars_file_is_attributes_only(self) -> None:
        text = 'location = "eastus"\n# admin_password = "commented"\nadmin_password = "FAKE-not-a-real-secret"\ntags = {\n  a = "b"\n}\n'

        assert HclParser().attributes(text) == {"location": "eastus", "admin_password": "FAKE-not-a-real-secret", "tags": "(complex)"}
