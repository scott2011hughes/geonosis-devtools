---
name: sqlalchemy-emr
description: SQLAlchemy async ORM patterns and schema-per-tenant isolation for the EMR/STP stack. Use alongside supabase-postgres-best-practices for raw PostgreSQL guidance. Covers tenant session management, async CRUD, repository pattern, and Alembic multi-schema migrations.
---

# SQLAlchemy — EMR Schema-Per-Tenant Patterns

This skill covers the ORM and tenant isolation layer. For raw PostgreSQL performance, indexing, RLS, and connection pooling see `supabase-postgres-best-practices`.

---

## Core Architecture

EMR uses **schema-per-tenant isolation** — each tenant gets a dedicated PostgreSQL schema (e.g. `tenant_abc123`). A shared `public` schema holds non-PHI reference data only. The tenant schema is set via `search_path` on every session — never assumed.

---

## Tenant-Scoped Session Dependency

This is the most critical pattern. Every FastAPI endpoint that touches tenant data must use a tenant-scoped session. Tenant ID always comes from the authenticated JWT — never from a request parameter.

```python
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text
from fastapi import Depends
from app.core.auth import get_current_tenant_id

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_tenant_db(
    tenant_id: str = Depends(get_current_tenant_id),
) -> AsyncGenerator[AsyncSession, None]:
    """Tenant-scoped database session — sets search_path on every session."""
    async with AsyncSessionLocal() as session:
        # Set tenant schema — must happen before any query
        await session.execute(text(f"SET search_path TO tenant_{tenant_id}, public"))
        yield session
        await session.rollback()

# Usage in route
@router.get("/clients", response_model=list[ClientResponseModel])
async def list_clients(
    db: AsyncSession = Depends(get_tenant_db),
):
    ...
```

**Never do this:**
```python
# BAD — no tenant scoping, queries public schema
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
```

---

## Model Definition

Use SQLAlchemy 2.0 `Mapped[T]` syntax. Models do not hardcode schema — `search_path` handles routing.

```python
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Client(Base):
    """Mental health client record. PHI fields — never log values."""
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    # PHI — encrypted at rest, never logged
    date_of_birth: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    insurance_id: Mapped[Optional[str]] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

class SessionNote(Base):
    """Clinical session note. Free-text PHI — encrypted at rest."""
    __tablename__ = "session_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(index=True)
    provider_id: Mapped[int] = mapped_column(index=True)
    content: Mapped[str] = mapped_column(Text)  # PHI — encrypted
    session_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

---

## Repository Pattern

Each entity gets a repository. Repositories receive an already-scoped session — they never create their own.

```python
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from app.models.client import Client
from app.schemas.client import ClientCreateRequestModel, ClientUpdateRequestModel

class ClientRepository:
    """Data access for Client records within the current tenant schema."""

    def __init__(self, db: AsyncSession):
        """Initialize with a tenant-scoped session."""
        self.db = db

    async def get_by_id(self, client_id: int) -> Optional[Client]:
        """Fetch client by primary key — returns None if not found."""
        stmt = select(Client).where(Client.id == client_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_external_id(self, external_id: str) -> Optional[Client]:
        """Fetch client by external ID — used for idempotent creates."""
        stmt = select(Client).where(Client.external_id == external_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self, skip: int = 0, limit: int = 100) -> list[Client]:
        """List active clients with pagination."""
        stmt = (
            select(Client)
            .where(Client.is_active == True)
            .order_by(Client.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, client_in: ClientCreateRequestModel) -> Client:
        """Create new client record."""
        client = Client(**client_in.model_dump())
        self.db.add(client)
        await self.db.flush()
        await self.db.refresh(client)
        return client

    async def update(self, client: Client, update_in: ClientUpdateRequestModel) -> Client:
        """Update client fields — only sets provided values."""
        for field, value in update_in.model_dump(exclude_unset=True).items():
            setattr(client, field, value)
        await self.db.flush()
        await self.db.refresh(client)
        return client

    async def soft_delete(self, client: Client) -> None:
        """Soft delete — sets is_active=False, preserves record for audit."""
        client.is_active = False
        await self.db.flush()
```

---

## Service Layer

Services own the transaction boundary and business logic. They wire together repositories.

```python
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.client import ClientRepository
from app.schemas.client import ClientCreateRequestModel, ClientResponseModel

class ClientService:
    """Business logic for client operations."""

    def __init__(self, db: AsyncSession):
        """Initialize with tenant-scoped session."""
        self.repo = ClientRepository(db)
        self.db = db

    async def create_client(self, client_in: ClientCreateRequestModel) -> ClientResponseModel:
        """Create client — idempotent on external_id."""
        # Check for existing client before creating
        existing = await self.repo.get_by_external_id(client_in.external_id)
        if existing:
            return ClientResponseModel.model_validate(existing)

        client = await self.repo.create(client_in)
        await self.db.commit()
        return ClientResponseModel.model_validate(client)
```

---

## Alembic Multi-Schema Migrations

Alembic must run migrations against every tenant schema. Never use autogenerate against the public schema for tenant tables.

```python
# alembic/env.py

import re
from sqlalchemy import text

def get_tenant_schemas(connection) -> list[str]:
    """Discover all tenant schemas from the database."""
    result = connection.execute(
        text("SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'tenant_%'")
    )
    return [row[0] for row in result]

def run_migrations_online():
    """Run migrations against all tenant schemas."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Run public schema migrations first
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema="public",
        )
        with context.begin_transaction():
            context.run_migrations()

        # Run against each tenant schema
        for schema in get_tenant_schemas(connection):
            connection.execute(text(f"SET search_path TO {schema}"))
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                include_schemas=True,
                version_table_schema=schema,
            )
            with context.begin_transaction():
                context.run_migrations()
```

---

## Tenant Isolation Testing — Mandatory

Every repository test must verify cross-tenant data cannot be accessed. This is a HIPAA requirement.

```python
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

@pytest.mark.asyncio
async def test_tenant_isolation(tenant_a_db: AsyncSession, tenant_b_db: AsyncSession):
    """Verify tenant A cannot see tenant B records — HIPAA boundary test."""
    # Create client in tenant A
    client = await ClientRepository(tenant_a_db).create(sample_client_data)
    await tenant_a_db.commit()

    # Verify tenant B session cannot see tenant A's client
    found = await ClientRepository(tenant_b_db).get_by_id(client.id)
    assert found is None, "HIPAA VIOLATION: cross-tenant data leak detected"
```

---

## Audit Log Pattern (Placeholder)

Every PHI access must be logged. Pattern TBD pending Patrick's input on audit log requirements.

```python
# TODO — implement with Patrick
# Required fields per HIPAA:
# - who (user_id, role)
# - what (table, record_id, operation)
# - when (timestamp with timezone)
# - from where (endpoint, ip)
# - tenant_id
```

---

## Notes

- `flush()` writes to transaction without committing — use inside repositories
- `commit()` belongs in the service layer, never in repositories
- `expire_on_commit=False` on session factory — prevents lazy load errors after commit in async context
- Always use `pool_pre_ping=True` — Azure PostgreSQL drops idle connections aggressively
