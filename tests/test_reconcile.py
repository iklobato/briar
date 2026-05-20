"""Reconciler integration tests — stub HTTP client, no network."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

from briar.errors import ConfigError
from briar.iac import ConfigFile, destroy_all, reconcile
from briar.iac.engine import summarise_ops


class _StubClient:
    """In-memory stand-in for `ApiClient` that records every call."""

    def __init__(self, prepopulate: Optional[Dict[str, List[Dict]]] = None):
        self.calls: List[tuple] = []
        self.existing: Dict[str, List[Dict]] = prepopulate or {}
        self.create_counter = 0

    def list_all(
        self,
        base_path: str,
        query: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        return self.existing.get(base_path, [])

    def request(
        self,
        method: str,
        path: str,
        body: Optional[Any] = None,
        query: Optional[Dict[str, Any]] = None,
    ) -> Any:
        self.calls.append((method, path, body))
        if method == "POST":
            self.create_counter += 1
            return {"id": f"uuid-{self.create_counter}", **(body or {})}
        if method == "PATCH":
            return {"id": path.rstrip("/").split("/")[-1], **(body or {})}
        return None


def _impl_config() -> ConfigFile:
    bundle = {
        "version": 1,
        "llm_models": [{
            "key": "m", "name": "test-model",
            "provider_key": "anthropic",
            "display_name": "claude",
            "default_params": {"temperature": 0.2},
        }],
        "sources": [{
            "key": "s", "name": "test-issues", "kind": "github",
            "config": {"owner": "o", "repo": "r"},
            "credential_binding": {
                "kind": "oauth_connection", "provider": "github",
            },
        }],
        "tools": [{
            "key": "t1", "name": "tool-1",
            "implementation_ref": "tools_x.foo",
            "side_effect": "read",
        }],
        "agents": [{
            "key": "a", "name": "test-agent",
            "llm_model_key": "m",
            "tool_keys": ["t1"],
            "source_keys": ["s"],
            "role": "test", "goal": "test",
        }],
        "workflows": [{
            "key": "w", "name": "test-wf",
            "graph": {
                "process": "sequential",
                "entry": "n1",
                "nodes": [
                    {"id": "n1", "kind": "agent", "agent_key": "a"},
                ],
            },
        }],
        "triggers": [{
            "key": "tr", "name": "test-trigger",
            "kind": "github_webhook",
            "workflow_key": "w",
        }],
    }
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False,
    ) as f:
        json.dump(bundle, f)
        path = f.name
    return ConfigFile.load(Path(path))


class PlanTests(unittest.TestCase):
    def test_fresh_plan_with_provider(self) -> None:
        client = _StubClient({
            "/api/v1/llm/providers/":
                [{"id": "p1", "name": "Anthropic", "kind": "anthropic"}]
        })
        plan = reconcile(client, _impl_config(), dry_run=True)
        ops = summarise_ops(plan)
        self.assertEqual(ops["create"], 6)

    def test_plan_lenient_without_provider(self) -> None:
        client = _StubClient()
        plan = reconcile(client, _impl_config(), dry_run=True)
        # Plan should NOT raise, even though `anthropic` is missing.
        self.assertEqual(sum(summarise_ops(plan).values()), 6)


class ApplyTests(unittest.TestCase):
    def test_dependency_order_and_substitution(self) -> None:
        client = _StubClient({
            "/api/v1/llm/providers/":
                [{"id": "p1", "name": "anthropic"}]
        })
        reconcile(client, _impl_config(), dry_run=False)
        posts = [c for c in client.calls if c[0] == "POST"]
        paths = [c[1] for c in posts]
        self.assertEqual(paths, [
            "/api/v1/llm/models/",
            "/api/v1/sources/",
            "/api/v1/tools/",
            "/api/v1/agents/",
            "/api/v1/workflows/",
            "/api/v1/triggers/",
        ])

        agent_body = next(
            c[2] for c in posts if c[1] == "/api/v1/agents/"
        )
        self.assertTrue(agent_body["llm_model"].startswith("uuid-"))
        self.assertTrue(all(
            t.startswith("uuid-") for t in agent_body["tool_ids"]
        ))

        wf_body = next(
            c[2] for c in posts if c[1] == "/api/v1/workflows/"
        )
        for node in wf_body["graph"]["nodes"]:
            if node["kind"] == "agent":
                self.assertTrue(node["agent_id"].startswith("uuid-"))

        tr_body = next(
            c[2] for c in posts if c[1] == "/api/v1/triggers/"
        )
        self.assertTrue(tr_body["target_workflow"].startswith("uuid-"))

    def test_apply_strict_when_provider_missing(self) -> None:
        client = _StubClient()
        with self.assertRaises(ConfigError):
            reconcile(client, _impl_config(), dry_run=False)


class DestroyTests(unittest.TestCase):
    def test_reverse_order(self) -> None:
        client = _StubClient({
            "/api/v1/triggers/":   [{"id": "x", "name": "test-trigger"}],
            "/api/v1/workflows/":  [{"id": "x", "name": "test-wf"}],
            "/api/v1/agents/":     [{"id": "x", "name": "test-agent"}],
            "/api/v1/tools/":      [{"id": "x", "name": "tool-1"}],
            "/api/v1/sources/":    [{"id": "x", "name": "test-issues"}],
            "/api/v1/llm/models/": [{"id": "x", "name": "test-model"}],
        })
        rows = destroy_all(client, _impl_config())
        deletes = [c[1] for c in client.calls if c[0] == "DELETE"]
        self.assertTrue(deletes[0].startswith("/api/v1/triggers/"))
        self.assertTrue(deletes[-1].startswith("/api/v1/llm/models/"))
        self.assertTrue(all(status == "deleted" for _, _, status in rows))


if __name__ == "__main__":
    unittest.main()
