"""Postgres-backed knowledge store, on SQLAlchemy 2.0.

Two tables under the `briar_knowledge` namespace:
- `briar_knowledge`         — current snapshot, one row per blob (UPSERT on put)
- `briar_knowledge_history` — append-only audit log (one row per put)

Runtime access goes through a scoped role (`briar_kb`) that holds only
`SELECT/INSERT/UPDATE/DELETE` on those two tables. Bootstrap (CREATE
TABLE + CREATE ROLE + GRANT) is done once by a higher-privilege admin
DSN via `StorePostgres.bootstrap_admin(...)`.

Connection management is a process-wide SQLAlchemy `Engine` (one per
distinct DSN). The pool replaces the previous per-call
`psycopg.connect()` pattern — every `get / put / list / delete /
fingerprint / put_if_changed` borrows a connection from the pool
instead of opening a fresh slot. That removes the "too many
connections" failure mode that the old retry loop was papering over.

`pool_pre_ping` validates a connection before checkout (recovers
silently from idle-dead conns on managed PG); `pool_recycle` cycles
preemptively. Both replace the bespoke retry logic that used to live
in `_connect()`."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import ClassVar, Dict, Iterable, List, Optional

from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from briar.errors import CliError
from briar.storage._models import Base, KnowledgeBlob, KnowledgeHistory
from briar.storage.base import KnowledgeRef, KnowledgeStore, PutIfChangedResult, StoreBinding


log = logging.getLogger(__name__)


def _company_task_from(blob_name: str) -> tuple[str, str]:
    """`knowledge:acme.prfix` → ("acme", "prfix"). Bare `acme` → ("acme", "")."""
    head, sep, tail = blob_name.partition(":")
    body = tail if sep else head
    company, dot, task = body.partition(".")
    return company, task if dot else ""


def _normalize_dsn(dsn: str) -> str:
    """SQLAlchemy 2 needs the explicit `+psycopg` driver tag to pick psycopg 3.
    Operators (and existing env files) ship the bare `postgresql://` /
    `postgres://` form — rewrite in-place so the public API stays
    unchanged. Anything else (already-tagged, sqlite, …) passes through."""
    if dsn.startswith("postgresql+"):
        return dsn
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://"):]
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn[len("postgres://"):]
    return dsn


class StorePostgres(KnowledgeStore):
    """KnowledgeStore implementation backed by Postgres.

    DSN is passed at construction time. The store does NOT create
    tables on init — see `bootstrap_admin` for the one-time setup.
    Runtime calls assume the schema + scoped role already exist."""

    name = "postgres"

    # Process-wide engines + session factories, keyed by the *original*
    # DSN string. Two stores constructed against the same DSN share a
    # pool — important because dashboard + scheduler + agent runner each
    # build their own StorePostgres instance and we don't want N pools
    # per process.
    _engines:  ClassVar[Dict[str, Engine]]       = {}
    _sessions: ClassVar[Dict[str, sessionmaker]] = {}

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise RuntimeError("StorePostgres: empty DSN — set BRIAR_DATABASE_URL")
        # Keep the original DSN on the instance — `from_binding` tests
        # and operator-facing error messages assert against this exact
        # string. Normalization for SQLAlchemy happens at engine-build
        # time only.
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

    # ---- engine / session lifecycle --------------------------------------

    def _engine(self) -> Engine:
        """Get-or-build the process-wide Engine for this DSN.

        Pool sizing: defaults to 4 + 2 overflow = 6 max per process.
        Three processes (dashboard, scheduler, agent runner) × 6 = 18
        slots, comfortably under DO managed PG's ~22 non-superuser
        budget on the small tier.

        `pool_pre_ping` re-validates before checkout — replaces the
        previous transient-error retry loop. `pool_recycle=1800` cycles
        connections every 30 minutes so we never hand out one that the
        server has silently dropped."""
        eng = type(self)._engines.get(self._dsn)
        if eng is not None:
            return eng

        eng = create_engine(
            _normalize_dsn(self._dsn),
            pool_size=int(os.environ.get("BRIAR_PG_POOL_SIZE", "4")),
            max_overflow=int(os.environ.get("BRIAR_PG_POOL_OVERFLOW", "2")),
            pool_timeout=10,
            pool_recycle=1800,
            pool_pre_ping=True,
            future=True,
        )
        type(self)._engines[self._dsn]  = eng
        # `expire_on_commit=False` so callers can read attributes off a
        # returned row after the `with session.begin()` block exits.
        # We don't return ORM instances across that boundary today, but
        # it's the safer default and matches how the rest of the code
        # already builds plain return values from input.
        type(self)._sessions[self._dsn] = sessionmaker(eng, expire_on_commit=False)
        return eng

    def _session(self) -> Session:
        self._engine()  # populate _sessions
        session: Session = type(self)._sessions[self._dsn]()
        return session

    # ---- read side -------------------------------------------------------

    def get(self, blob_name: str) -> str:
        with self._session() as s:
            value = s.scalar(
                select(KnowledgeBlob.content).where(KnowledgeBlob.blob_name == blob_name)
            )
        return value or ""

    def get_many(self, names: Iterable[str]) -> Dict[str, str]:
        """Single-round-trip bulk fetch. Replaces the N+1 pattern at
        every call site that did `for name in names: store.get(name)`
        (KnowledgeSplicer, dashboard collectors, plan context)."""
        if not names:
            return {}
        with self._session() as s:
            rows = s.execute(
                select(KnowledgeBlob.blob_name, KnowledgeBlob.content)
                .where(KnowledgeBlob.blob_name.in_(list(names)))
            ).all()
        return {name: content for name, content in rows}

    def fingerprint(self, blob_name: str) -> str:
        """Server-side md5 so we don't drag the whole blob across the
        wire just to compare. Postgres ships `md5()` in core."""
        with self._session() as s:
            value = s.scalar(
                select(func.md5(KnowledgeBlob.content))
                .where(KnowledgeBlob.blob_name == blob_name)
            )
        return value or ""

    def list(self, prefix: str = "") -> List[KnowledgeRef]:
        stmt = select(
            KnowledgeBlob.blob_name,
            KnowledgeBlob.category,
            KnowledgeBlob.byte_count,
            KnowledgeBlob.updated_at,
            KnowledgeBlob.company,
            KnowledgeBlob.task,
        ).order_by(KnowledgeBlob.blob_name)
        if prefix:
            stmt = stmt.where(KnowledgeBlob.blob_name.like(f"{prefix}%"))
        with self._session() as s:
            rows = s.execute(stmt).all()
        return [
            KnowledgeRef(
                name=blob_name,
                category=category,
                byte_count=byte_count,
                updated_at=updated_at.isoformat() if updated_at else "",
                extra={"company": company, "task": task},
            )
            for (blob_name, category, byte_count, updated_at, company, task) in rows
        ]

    # ---- write side ------------------------------------------------------

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
        with self._session() as s, s.begin():
            s.execute(self._upsert_stmt(blob_name, cat, company, task, content, byte_count))
            s.add(KnowledgeHistory(
                blob_name=blob_name,
                category=cat,
                content=content,
                byte_count=byte_count,
            ))
        return KnowledgeRef(
            name=blob_name,
            category=cat,
            byte_count=byte_count,
            updated_at="",
            extra={"company": company, "task": task},
        )

    def put_if_changed(self, blob_name: str, content: str, category: str = "") -> PutIfChangedResult:
        """Compare md5 server-side AND do the write in a single
        connection/transaction.

        Crucial on DO managed Postgres where the user-role connection
        slot budget is tight — borrowing one pool connection (vs two
        independent fingerprint+put calls) halves slot pressure under
        burst load. The compare-and-set is also atomic: a concurrent
        writer can't sneak a change in between our read and our write."""
        cat = category or KnowledgeRef.category_of(blob_name)
        company, task = _company_task_from(blob_name)
        byte_count = len(content)
        new_hash = hashlib.md5(content.encode("utf-8")).hexdigest()

        with self._session() as s, s.begin():
            existing_hash = s.scalar(
                select(func.md5(KnowledgeBlob.content))
                .where(KnowledgeBlob.blob_name == blob_name)
            )
            existing_hash = existing_hash or ""

            if existing_hash and existing_hash == new_hash:
                log.info(
                    "pg-store skip: blob=%s bytes=%d hash=%s — content unchanged",
                    blob_name,
                    byte_count,
                    new_hash,
                )
                return PutIfChangedResult(
                    wrote=False,
                    byte_count=byte_count,
                    new_hash=new_hash,
                    prev_hash=existing_hash,
                )

            log.info(
                "pg-store put: blob=%s category=%s company=%s task=%s bytes=%d prev_hash=%s",
                blob_name,
                cat,
                company,
                task or "(default)",
                byte_count,
                existing_hash or "(none)",
            )
            s.execute(self._upsert_stmt(blob_name, cat, company, task, content, byte_count))
            s.add(KnowledgeHistory(
                blob_name=blob_name,
                category=cat,
                content=content,
                byte_count=byte_count,
            ))

        ref = KnowledgeRef(
            name=blob_name,
            category=cat,
            byte_count=byte_count,
            updated_at="",
            extra={"company": company, "task": task},
        )
        return PutIfChangedResult(
            wrote=True,
            byte_count=byte_count,
            new_hash=new_hash,
            prev_hash=existing_hash,
            ref=ref,
        )

    def delete(self, blob_name: str) -> bool:
        with self._session() as s, s.begin():
            row = s.get(KnowledgeBlob, blob_name)
            if row is None:
                return False
            s.delete(row)
        log.debug("postgres delete: blob=%s removed=True", blob_name)
        return True

    # ---- internals --------------------------------------------------------

    @staticmethod
    def _upsert_stmt(blob_name: str, category: str, company: str, task: str, content: str, byte_count: int):
        """Build a single `INSERT ... ON CONFLICT DO UPDATE` for the
        current-snapshot table. The history insert is a separate
        `Session.add` in the caller — they share one transaction via
        `with session.begin()`."""
        return pg_insert(KnowledgeBlob).values(
            blob_name=blob_name,
            category=category,
            company=company,
            task=task,
            content=content,
            byte_count=byte_count,
        ).on_conflict_do_update(
            index_elements=[KnowledgeBlob.blob_name],
            set_={
                "category": category,
                "company": company,
                "task": task,
                "content": content,
                "byte_count": byte_count,
                "updated_at": func.now(),
            },
        )

    # ---- one-time bootstrap (admin path) ---------------------------------

    @classmethod
    def bootstrap_admin(cls, admin_dsn: str, briar_kb_password: str) -> None:
        """Run once with a high-privilege DSN (e.g. doadmin). Creates
        both tables (via SQLAlchemy `metadata.create_all`), creates the
        `briar_kb` role (or resets its password), grants scoped DML on
        the two tables.

        Tables come from the ORM models; the role + grants are raw SQL
        because SQLAlchemy doesn't model PG roles, and `CREATE ROLE`
        rejects bound parameters for the password (so we use
        `psycopg.sql.Literal` for safe escaping at compose time)."""
        log.info("bootstrap: connecting via admin DSN (host redacted)")
        admin = create_engine(_normalize_dsn(admin_dsn), isolation_level="AUTOCOMMIT", future=True)

        log.info("bootstrap: creating tables + indexes (idempotent)")
        Base.metadata.create_all(admin)

        log.info("bootstrap: ensuring role briar_kb exists with the supplied password")
        from psycopg import sql

        password_literal = sql.Literal(briar_kb_password)
        raw = admin.raw_connection()
        try:
            cur = raw.cursor()
            try:
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'briar_kb'")
                exists = cur.fetchone() is not None
                if not exists:
                    cur.execute(sql.SQL("CREATE ROLE briar_kb LOGIN PASSWORD {}").format(password_literal))
                else:
                    cur.execute(sql.SQL("ALTER ROLE briar_kb WITH LOGIN PASSWORD {}").format(password_literal))
                log.info("bootstrap: granting scoped DML on briar_knowledge[_history]")
                cur.execute(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON briar_knowledge          TO briar_kb;"
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON briar_knowledge_history  TO briar_kb;"
                    "GRANT USAGE, SELECT ON SEQUENCE briar_knowledge_history_id_seq   TO briar_kb;"
                )
            finally:
                cur.close()
        finally:
            raw.close()
        log.info("bootstrap: done")
