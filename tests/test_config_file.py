"""ConfigFile loader tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from briar.errors import ConfigError
from briar.iac import ConfigFile


def _write(payload: object) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    f.write(json.dumps(payload))
    f.close()
    return Path(f.name)


class LoadTests(unittest.TestCase):
    def test_empty_object(self) -> None:
        cfg = ConfigFile.load(_write({}))
        self.assertEqual(cfg.version, 1)
        self.assertEqual(cfg.agents, [])

    def test_populated(self) -> None:
        cfg = ConfigFile.load(_write({
            "agents": [
                {"key": "a", "name": "x", "llm_model_key": "m"},
            ],
            "workflows": [{
                "key": "w", "name": "y",
                "graph": {
                    "entry": "n1",
                    "nodes": [
                        {"id": "n1", "kind": "agent", "agent_key": "a"},
                    ],
                },
            }],
        }))
        self.assertEqual(len(cfg.agents), 1)
        self.assertEqual(len(cfg.workflows), 1)

    def test_missing_file(self) -> None:
        with self.assertRaises(ConfigError):
            ConfigFile.load(Path("/tmp/does-not-exist-xyz.json"))

    def test_non_object_root(self) -> None:
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.write("[]")
        f.close()
        with self.assertRaises(ConfigError):
            ConfigFile.load(Path(f.name))

    def test_section_dispatch(self) -> None:
        cfg = ConfigFile.load(_write({
            "agents": [
                {"key": "a", "name": "x", "llm_model_key": "m"},
            ],
        }))
        self.assertEqual(len(cfg.section("agents")), 1)

    def test_unknown_field_rejected(self) -> None:
        # Pydantic's strict mode catches typos that would otherwise
        # silently drop on the server.
        with self.assertRaises(ConfigError):
            ConfigFile.load(_write({
                "agents": [
                    {"key": "a", "name": "x", "llm_model_key": "m",
                     "typo_field": "oops"},
                ],
            }))

    def test_unknown_section(self) -> None:
        cfg = ConfigFile.load(_write({}))
        with self.assertRaises(ConfigError):
            cfg.section("bogus")


if __name__ == "__main__":
    unittest.main()
