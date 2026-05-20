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
from typing import List

from briar.storage.base import KnowledgeRef, KnowledgeStore


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

    # ---- runtime ----------------------------------------------------------

    def put(self, blob_name: str, content: str, category: str = "") -> KnowledgeRef:
        cat = category or KnowledgeRef.category_of(blob_name)
        company, task = _company_task_from(blob_name)
        byte_count = len(content)
        log.debug("postgres put: blob=%s category=%s bytes=%d", blob_name, cat, byte_count)
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
        # Lazy import so the module loads even when psycopg isn't installed
        # (e.g. file-only users running the dashboard locally).
        import psycopg

        return psycopg.connect(self._dsn, autocommit=False)

    # ---- one-time bootstrap (admin path) ---------------------------------

    @classmethod
    def bootstrap_admin(cls, admin_dsn: str, briar_kb_password: str) -> None:
        """Run once with a high-privilege DSN (e.g. doadmin). Creates
        both tables, creates the `briar_kb` role (or resets its
        password), grants scoped DML on the two tables."""
        import psycopg

        log.info("bootstrap: connecting via admin DSN (host redacted)")
        with psycopg.connect(admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            log.info("bootstrap: creating tables + indexes (idempotent)")
            cur.execute(_DDL_TABLES)
            log.info("bootstrap: ensuring role briar_kb exists with the supplied password")
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'briar_kb'")
            if cur.fetchone() is None:
                cur.execute("CREATE ROLE briar_kb LOGIN PASSWORD %s", (briar_kb_password,))
            else:
                cur.execute("ALTER ROLE briar_kb WITH LOGIN PASSWORD %s", (briar_kb_password,))
            log.info("bootstrap: granting scoped DML on briar_knowledge[_history]")
            cur.execute(_DDL_GRANTS)
        log.info("bootstrap: done")
