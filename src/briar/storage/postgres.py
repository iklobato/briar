"""Postgres-backed knowledge store.

Two tables under the `briar_knowledge` namespace:
- `briar_knowledge`         — current snapshot, one row per blob (UPSERT on put)
- `briar_knowledge_history` — append-only audit log (one row per put)

Runtime access goes through a scoped role (`briar_kb`) that holds only
`SELECT/INSERT/UPDATE/DELETE` on those two tables. Bootstrap (CREATE
TABLE + CREATE ROLE + GRANT) is done once by a higher-privilege admin
DSN via `StorePostgres.bootstrap_admin(...)`."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

from briar.errors import CliError
from briar.storage.base import KnowledgeRef, KnowledgeStore, StoreBinding


log = logging.getLogger(__name__)


_DDL_TABLES = """
CREATE TABLE IF NOT EXISTS briar_knowledge (
    blob_name   TEXT PRIMARY KEY,
    category    TEXT NOT NULL,
    company     TEXT NOT NULL DEFAULT '',
    task        TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL,
    byte_count  INT  NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS briar_knowledge_category_idx ON briar_knowledge (category);
CREATE INDEX IF NOT EXISTS briar_knowledge_company_idx  ON briar_knowledge (company);

CREATE TABLE IF NOT EXISTS briar_knowledge_history (
    id          BIGSERIAL PRIMARY KEY,
    blob_name   TEXT NOT NULL,
    category    TEXT NOT NULL,
    content     TEXT NOT NULL,
    byte_count  INT NOT NULL,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS briar_knowledge_history_blob_at_idx
    ON briar_knowledge_history (blob_name, snapshot_at DESC);
"""

_DDL_GRANTS = """
GRANT SELECT, INSERT, UPDATE, DELETE ON briar_knowledge          TO briar_kb;
GRANT SELECT, INSERT, UPDATE, DELETE ON briar_knowledge_history  TO briar_kb;
GRANT USAGE, SELECT ON SEQUENCE briar_knowledge_history_id_seq   TO briar_kb;
"""


def _company_task_from(blob_name: str) -> tuple[str, str]:
    """`knowledge:acme.prfix` → ("acme", "prfix"). Bare `acme` → ("acme", "")."""
    head, sep, tail = blob_name.partition(":")
    body = tail if sep else head
    company, dot, task = body.partition(".")
    return company, task if dot else ""


class StorePostgres(KnowledgeStore):
    """KnowledgeStore implementation backed by Postgres.

    DSN is passed at construction time. The store does NOT create
    tables on init — see `bootstrap_admin` for the one-time setup.
    Runtime calls assume the schema + scoped role already exist."""

    name = "postgres"

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise RuntimeError("StorePostgres: empty DSN — set BRIAR_DATABASE_URL")
        self._dsn = dsn

    @classmethod
    def from_binding(cls, binding: StoreBinding, *, default_root: Optional[Path] = None) -> "StorePostgres":
        """Resolve a DSN from (in priority order):

        1. ``binding.config["dsn_env"]`` — explicit YAML override:
           ``knowledge: {store: postgres, config: {dsn_env: PROD_KB_PG}}``
        2. ``BRIAR_{COMPANY}_DATABASE_URL`` — convention-based per-company
           env (e.g. ``BRIAR_ACME_DATABASE_URL``)
        3. ``BRIAR_DATABASE_URL`` — global fallback (existing single-DSN
           deployments unchanged)

        Raises ``CliError`` naming every key tried, in order, so the
        operator sees exactly what to set."""
        from briar.env_vars import CredEnv

        tried: List[str] = []
        dsn = ""

        config_env = binding.config.get("dsn_env", "")
        if config_env:
            tried.append(f"${config_env} (binding.config.dsn_env)")
            dsn = os.environ.get(config_env, "")

        if not dsn and binding.company:
            per_company_key = CredEnv.BRIAR_DATABASE_URL_FOR_COMPANY.for_company(binding.company)
            tried.append(f"${per_company_key}")
            dsn = os.environ.get(per_company_key, "")

        if not dsn:
            tried.append(f"${CredEnv.BRIAR_DATABASE_URL.value}")
            dsn = CredEnv.BRIAR_DATABASE_URL.read()

        if not dsn:
            raise CliError("store 'postgres' requires a DSN; tried (in order): " + ", ".join(tried))

        return cls(dsn)

    # ---- runtime ----------------------------------------------------------

    def put(self, blob_name: str, content: str, category: str = "") -> KnowledgeRef:
        cat = category or KnowledgeRef.category_of(blob_name)
        company, task = _company_task_from(blob_name)
        byte_count = len(content)
        log.info(
            "pg-store put: blob=%s category=%s company=%s task=%s bytes=%d",
            blob_name,
            cat,
            company,
            task or "(default)",
            byte_count,
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO briar_knowledge (blob_name, category, company, task, content, byte_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (blob_name) DO UPDATE
                SET category   = EXCLUDED.category,
                    company    = EXCLUDED.company,
                    task       = EXCLUDED.task,
                    content    = EXCLUDED.content,
                    byte_count = EXCLUDED.byte_count,
                    updated_at = now()
                """,
                (blob_name, cat, company, task, content, byte_count),
            )
            cur.execute(
                """
                INSERT INTO briar_knowledge_history (blob_name, category, content, byte_count)
                VALUES (%s, %s, %s, %s)
                """,
                (blob_name, cat, content, byte_count),
            )
            conn.commit()
        return KnowledgeRef(
            name=blob_name,
            category=cat,
            byte_count=byte_count,
            updated_at="",
            extra={"company": company, "task": task},
        )

    def get(self, blob_name: str) -> str:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT content FROM briar_knowledge WHERE blob_name = %s", (blob_name,))
            row = cur.fetchone()
        if row is None:
            return ""
        return str(row[0])

    def fingerprint(self, blob_name: str) -> str:
        """Server-side md5 so we don't drag the whole blob across the wire
        just to compare. Postgres ships `md5()` in core."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT md5(content) FROM briar_knowledge WHERE blob_name = %s", (blob_name,))
            row = cur.fetchone()
        if row is None:
            return ""
        return str(row[0])

    def put_if_changed(self, blob_name: str, content: str, category: str = "") -> "PutIfChangedResult":
        """Compare md5 server-side AND do the write in a single connection.

        Crucial on DO managed Postgres where the user-role connection slot
        budget is tight — opening two separate connections (fingerprint,
        put) doubles slot pressure under burst load and can hit
        `FATAL: remaining connection slots are reserved for roles with
        the SUPERUSER attribute`. One transaction here means one slot
        held for the duration of the operation. The compare-and-set is
        also atomic, so a concurrent writer can't sneak a change in
        between our read and our write."""
        from briar.storage.base import PutIfChangedResult

        cat = category or KnowledgeRef.category_of(blob_name)
        company, task = _company_task_from(blob_name)
        byte_count = len(content)
        import hashlib

        new_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT md5(content) FROM briar_knowledge WHERE blob_name = %s", (blob_name,))
            row = cur.fetchone()
            existing_hash = str(row[0]) if row else ""
            if existing_hash and existing_hash == new_hash:
                log.info(
                    "pg-store skip: blob=%s bytes=%d hash=%s — content unchanged",
                    blob_name,
                    byte_count,
                    new_hash,
                )
                return PutIfChangedResult(wrote=False, byte_count=byte_count, new_hash=new_hash, prev_hash=existing_hash)
            log.info(
                "pg-store put: blob=%s category=%s company=%s task=%s bytes=%d prev_hash=%s",
                blob_name,
                cat,
                company,
                task or "(default)",
                byte_count,
                existing_hash or "(none)",
            )
            cur.execute(
                """
                INSERT INTO briar_knowledge (blob_name, category, company, task, content, byte_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (blob_name) DO UPDATE
                SET category   = EXCLUDED.category,
                    company    = EXCLUDED.company,
                    task       = EXCLUDED.task,
                    content    = EXCLUDED.content,
                    byte_count = EXCLUDED.byte_count,
                    updated_at = now()
                """,
                (blob_name, cat, company, task, content, byte_count),
            )
            cur.execute(
                """
                INSERT INTO briar_knowledge_history (blob_name, category, content, byte_count)
                VALUES (%s, %s, %s, %s)
                """,
                (blob_name, cat, content, byte_count),
            )
            conn.commit()
        ref = KnowledgeRef(
            name=blob_name,
            category=cat,
            byte_count=byte_count,
            updated_at="",
            extra={"company": company, "task": task},
        )
        return PutIfChangedResult(wrote=True, byte_count=byte_count, new_hash=new_hash, prev_hash=existing_hash, ref=ref)

    def list(self, prefix: str = "") -> List[KnowledgeRef]:
        with self._connect() as conn, conn.cursor() as cur:
            if prefix:
                cur.execute(
                    """
                    SELECT blob_name, category, byte_count, updated_at, company, task
                    FROM briar_knowledge
                    WHERE blob_name LIKE %s
                    ORDER BY blob_name
                    """,
                    (prefix + "%",),
                )
            else:
                cur.execute(
                    """
                    SELECT blob_name, category, byte_count, updated_at, company, task
                    FROM briar_knowledge
                    ORDER BY blob_name
                    """
                )
            rows = cur.fetchall()
        return [
            KnowledgeRef(
                name=name,
                category=category,
                byte_count=byte_count,
                updated_at=updated_at.isoformat() if updated_at else "",
                extra={"company": company, "task": task},
            )
            for (name, category, byte_count, updated_at, company, task) in rows
        ]

    def delete(self, blob_name: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM briar_knowledge WHERE blob_name = %s", (blob_name,))
            removed = bool(cur.rowcount > 0)
            conn.commit()
        log.debug("postgres delete: blob=%s removed=%s", blob_name, removed)
        return removed

    # ---- internals --------------------------------------------------------

    def _connect(self):
        """Open a connection, retrying once on transient slot exhaustion.

        DO managed Postgres on the smallest tier has ~22 non-superuser
        connection slots. When several (dashboard + scheduler + api) all
        spike at once we hit `FATAL: remaining connection slots are
        reserved for roles with the SUPERUSER attribute`. The error is
        transient: a brief sleep typically lets a peer's `with` block
        close and free a slot. Three attempts with linear back-off keep
        the scheduler fires + dashboard renders alive without masking a
        real outage (after 3 we propagate the original error and the
        caller's `logger.exception` records the full traceback)."""
        import time

        import psycopg

        last_exc: Exception = RuntimeError("unreachable")
        for attempt in range(3):
            try:
                return psycopg.connect(self._dsn, autocommit=False)
            except psycopg.OperationalError as exc:
                msg = str(exc).lower()
                transient = ("remaining connection slots" in msg) or ("too many connections" in msg) or ("connection timed out" in msg)
                if not transient:
                    raise
                last_exc = exc
                wait = 0.25 * (attempt + 1)
                log.warning("pg-store connect transient failure (attempt %d/3): %s — retrying in %.2fs", attempt + 1, exc, wait)
                time.sleep(wait)
        raise last_exc

    # ---- one-time bootstrap (admin path) ---------------------------------

    @classmethod
    def bootstrap_admin(cls, admin_dsn: str, briar_kb_password: str) -> None:
        """Run once with a high-privilege DSN (e.g. doadmin). Creates
        both tables, creates the `briar_kb` role (or resets its
        password), grants scoped DML on the two tables."""
        import psycopg

        from psycopg import sql

        log.info("bootstrap: connecting via admin DSN (host redacted)")
        with psycopg.connect(admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            log.info("bootstrap: creating tables + indexes (idempotent)")
            cur.execute(_DDL_TABLES)
            log.info("bootstrap: ensuring role briar_kb exists with the supplied password")
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'briar_kb'")
            # Postgres rejects parameter substitution in CREATE/ALTER ROLE
            # — the password must be an SQL literal. `psycopg.sql.Literal`
            # gives us safe string-escaping at compose time, no exposure to
            # injection.
            password_literal = sql.Literal(briar_kb_password)
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE ROLE briar_kb LOGIN PASSWORD {}").format(password_literal))
            else:
                cur.execute(sql.SQL("ALTER ROLE briar_kb WITH LOGIN PASSWORD {}").format(password_literal))
            log.info("bootstrap: granting scoped DML on briar_knowledge[_history]")
            cur.execute(_DDL_GRANTS)
        log.info("bootstrap: done")
