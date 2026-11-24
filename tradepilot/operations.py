from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, MetaData, String, Table, Column, create_engine, insert, select, update
from sqlalchemy.pool import StaticPool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def database_url() -> str:
    configured = os.getenv("TRADEPILOT_DATABASE_URL", "").strip()
    if configured:
        return configured
    path = os.getenv("TRADEPILOT_DB_PATH", "./data/tradepilot.sqlite3")
    if path == ":memory:":
        return "sqlite+pysqlite:///:memory:"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return "sqlite+pysqlite:///" + str(Path(path).resolve())


class OperationsStore:
    """Durable workflow/audit store. PostgreSQL is used when TRADEPILOT_DATABASE_URL is set."""

    def __init__(self, url: str | None = None):
        self.url = url or database_url()
        options: dict[str, Any] = {"pool_pre_ping": True}
        if self.url.endswith(":memory:"):
            options.update(poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.engine = create_engine(self.url, **options)
        self.metadata = MetaData()
        self.records = Table(
            "workflow_records", self.metadata,
            Column("id", String(80), primary_key=True),
            Column("kind", String(40), nullable=False),
            Column("status", String(60), nullable=False),
            Column("payload", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
        )
        self.audit = Table(
            "audit_events", self.metadata,
            Column("id", String(80), primary_key=True),
            Column("actor", String(120), nullable=False),
            Column("role", String(40), nullable=False),
            Column("action", String(100), nullable=False),
            Column("resource", String(160), nullable=False),
            Column("details", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self.jobs = Table(
            "background_jobs", self.metadata,
            Column("id", String(80), primary_key=True),
            Column("kind", String(60), nullable=False),
            Column("status", String(30), nullable=False),
            Column("result", JSON, nullable=True),
            Column("error", String(300), nullable=True),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
        )
        self.metadata.create_all(self.engine)

    @property
    def persistence_label(self) -> str:
        return "postgresql_durable" if self.url.startswith("postgresql") else "sqlite_ephemeral_or_local"

    def ready(self) -> bool:
        try:
            with self.engine.begin() as conn:
                conn.execute(select(self.records.c.id).limit(1))
            return True
        except Exception:
            return False

    def create_record(self, kind: str, status: str, payload: dict[str, Any], prefix: str) -> dict[str, Any]:
        now = _utcnow()
        record_id = f"{prefix}-{uuid4().hex[:12].upper()}"
        with self.engine.begin() as conn:
            conn.execute(insert(self.records).values(
                id=record_id, kind=kind, status=status, payload=payload,
                created_at=now, updated_at=now,
            ))
        return self.get_record(record_id) or {}

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(self.records).where(self.records.c.id == record_id)).mappings().first()
        if not row:
            return None
        result = dict(row)
        result["created_at"] = result["created_at"].isoformat()
        result["updated_at"] = result["updated_at"].isoformat()
        return result

    def update_record(self, record_id: str, status: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            conn.execute(update(self.records).where(self.records.c.id == record_id).values(
                status=status, payload=payload, updated_at=_utcnow(),
            ))
        return self.get_record(record_id)

    def list_records(self, kind: str | None = None) -> list[dict[str, Any]]:
        statement = select(self.records).order_by(self.records.c.created_at.desc())
        if kind:
            statement = statement.where(self.records.c.kind == kind)
        with self.engine.begin() as conn:
            rows = conn.execute(statement).mappings().all()
        return [self.get_record(row["id"]) for row in rows]

    def log(self, actor: str, role: str, action: str, resource: str, details: dict[str, Any] | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(self.audit).values(
                id=f"AUD-{uuid4().hex[:16].upper()}", actor=actor, role=role,
                action=action, resource=resource, details=details or {}, created_at=_utcnow(),
            ))

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(select(self.audit).order_by(self.audit.c.created_at.desc()).limit(limit)).mappings().all()
        result = []
        for row in rows:
            item = dict(row)
            item["created_at"] = item["created_at"].isoformat()
            result.append(item)
        return result

    def create_job(self, kind: str) -> dict[str, Any]:
        now = _utcnow()
        job_id = f"JOB-{uuid4().hex[:12].upper()}"
        with self.engine.begin() as conn:
            conn.execute(insert(self.jobs).values(id=job_id, kind=kind, status="queued", created_at=now, updated_at=now))
        return self.get_job(job_id) or {}

    def finish_job(self, job_id: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(self.jobs).where(self.jobs.c.id == job_id).values(
                status="failed" if error else "completed", result=result, error=error, updated_at=_utcnow(),
            ))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(self.jobs).where(self.jobs.c.id == job_id)).mappings().first()
        if not row:
            return None
        item = dict(row)
        item["created_at"] = item["created_at"].isoformat()
        item["updated_at"] = item["updated_at"].isoformat()
        return item
