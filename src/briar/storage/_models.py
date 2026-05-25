"""SQLAlchemy ORM models for the postgres `KnowledgeStore` backend.

Two tables, mirroring the manual DDL that previously lived as a SQL
string inside `StorePostgres`:

- `briar_knowledge`         — current snapshot, one row per blob
- `briar_knowledge_history` — append-only audit log, one row per put

Indices match what `_DDL_TABLES` declared. `Base.metadata.create_all`
on the admin engine reproduces today's bootstrap step bit-for-bit.

Kept in its own module so `storage/postgres.py` doesn't grow a model
section in addition to its query layer, and so tests can import the
models without dragging in the engine."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class KnowledgeBlob(Base):
    __tablename__ = "briar_knowledge"

    blob_name:  Mapped[str]      = mapped_column(String, primary_key=True)
    category:   Mapped[str]      = mapped_column(String, nullable=False)
    company:    Mapped[str]      = mapped_column(String, nullable=False, server_default="")
    task:       Mapped[str]      = mapped_column(String, nullable=False, server_default="")
    content:    Mapped[str]      = mapped_column(String, nullable=False)
    byte_count: Mapped[int]      = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("briar_knowledge_category_idx", "category"),
        Index("briar_knowledge_company_idx", "company"),
    )


class KnowledgeHistory(Base):
    __tablename__ = "briar_knowledge_history"

    id:          Mapped[int]      = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    blob_name:   Mapped[str]      = mapped_column(String, nullable=False)
    category:    Mapped[str]      = mapped_column(String, nullable=False)
    content:     Mapped[str]      = mapped_column(String, nullable=False)
    byte_count:  Mapped[int]      = mapped_column(Integer, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("briar_knowledge_history_blob_at_idx", "blob_name", "snapshot_at"),
    )


__all__ = ["Base", "KnowledgeBlob", "KnowledgeHistory"]
