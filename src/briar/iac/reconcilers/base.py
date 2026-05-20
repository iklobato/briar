"""`ResourceReconciler` Template Method base.

In its own module so concrete reconcilers can subclass without
triggering the package `__init__` (which itself imports the concretes
to assemble `RECONCILER_ORDER`)."""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional, Tuple

from briar.errors import ConfigError
from briar.http import ApiClient
from briar.iac.reference_map import ReferenceMap


class ResourceReconciler:
    kind: ClassVar[str] = ""
    base_path: ClassVar[str] = ""
    name_field: ClassVar[str] = "name"

    # ---- subclass extension points ---------------------------------------

    def project(
        self,
        spec: Dict[str, Any],
        refs: ReferenceMap,
    ) -> Dict[str, Any]:
        """Translate the config-file spec (with key refs) into an API body."""
        raise NotImplementedError

    # ---- default mechanics -----------------------------------------------

    def name_of(self, spec: Dict[str, Any]) -> str:
        value = spec.get(self.name_field) or spec.get("key")
        if not value:
            raise ConfigError(
                f"{self.kind} entry missing both "
                f"`{self.name_field}` and `key`: {spec}"
            )
        return str(value)

    def find_existing(
        self,
        client: ApiClient,
        name: str,
    ) -> Optional[Dict[str, Any]]:
        for it in client.list_all(self.base_path):
            if type(it) is not dict:
                continue
            if it.get(self.name_field) == name:
                return it
        return None

    def index_existing(
        self,
        client: ApiClient,
        refs: ReferenceMap,
    ) -> None:
        """Register every live resource under `(kind, name) → id`."""
        for it in client.list_all(self.base_path):
            if type(it) is not dict:
                continue
            name = it.get(self.name_field)
            uuid = it.get("id")
            if name and uuid:
                refs.remember(self.kind, str(name), str(uuid))

    def apply(
        self,
        client: ApiClient,
        spec: Dict[str, Any],
        refs: ReferenceMap,
    ) -> Tuple[str, str]:
        """Upsert. Returns (op, uuid) where op is create / update / noop."""
        body = self.project(spec, refs)
        name = body.get(self.name_field) or self.name_of(spec)
        existing = self.find_existing(client, name)
        if not existing:
            response = client.request("POST", self.base_path, body)
            uuid = response.get("id", "") if type(response) is dict else ""
            return "create", str(uuid)
        existing_id = str(existing.get("id", ""))
        diff = {k: v for k, v in body.items() if existing.get(k) != v}
        if not diff:
            return "noop", existing_id
        client.request("PATCH", f"{self.base_path}{existing_id}/", body)
        return "update", existing_id

    def destroy(
        self,
        client: ApiClient,
        spec: Dict[str, Any],
    ) -> bool:
        existing = self.find_existing(client, self.name_of(spec))
        if not existing:
            return False
        client.request("DELETE", f"{self.base_path}{existing['id']}/")
        return True
