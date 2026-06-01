# Phase 1 — The Wall (Multi-Tenancy + Identity)

> **Hand this file to Claude Code in VS Code with:**
> "Read `.specify/memory/constitution.md` and this file. Implement Phase 1 task by task. Pause for approval after each task before committing."

---

## Goal

Two businesses can sign up. Each has its own data. Business A can never see
Business B's data — and there are tests proving it. Modir can also tell, from
any incoming WhatsApp message, **which business it belongs to and whether the
sender is the owner or a customer**.

By the end of this phase, ten tables exist, every repository method is forced to
filter by `tenant_id`, dashboard users can sign up and log in with short-lived
JWTs, the identity resolver routes owners vs customers, the business-profile CRUD
works, and a failing test that *tries* to cross the wall is blocked.

This is the most important phase in the project. Nothing in Phases 2–9 is safe
to build until The Wall holds.

## Prerequisites

- [ ] Phase 0 is complete and merged. All 11 tasks landed; DoD met (see `docs/PHASE_0_DEFEND_IT.md`).
- [ ] `docker compose up` brings up a clean, healthy stack with no errors.
- [ ] `Base` ORM class exists in `backend/app/db/models.py` with `id`, `created_at`, `updated_at`.
- [ ] `Settings`, Vault (`resolve_secrets`), structlog, async session, and Alembic env are all wired (Phase 0).
- [ ] Vault dev mode is seeded; `seed_vault.sh` exists.

## What Phase 1 builds on (already exists from Phase 0)

Do **not** re-create these — extend them:

- `app/db/models.py::Base` — gives every model `id: UUID`, `created_at`, `updated_at`. New models inherit from it and add `tenant_id`.
- `app/db/session.py` — `create_engine()` (called in lifespan) and `get_db_session()` (FastAPI dependency).
- `app/infra/settings.py::Settings` — the single config class. Phase 1 adds `jwt_secret`, `jwt_algorithm`, `jwt_expiry_minutes`.
- `app/infra/vault.py::resolve_secrets()` — the `secrets_map`. Phase 1 adds the JWT signing secret here.
- `backend/alembic/env.py` — already points at `Base.metadata`; new models are picked up by `--autogenerate` automatically once imported.
- `app/main.py::lifespan` / `create_app()` — Phase 1 mounts the new routers in `create_app()`.

---

## The Identity Model (read this before you build)

Every WhatsApp message carries two numbers:

- **`to`** = destination → which business (`tenants.whatsapp_number`)
- **`from`** = sender → which role (lookup in `tenant_owners` for that tenant)
  - Found → `owner`
  - Not found → `customer` (auto-created, scoped to that tenant)

Two distinct tables that must never be confused:

| Table | Represents | Used for |
|-------|-----------|----------|
| `users` | Dashboard accounts (email + password) | Logging into the React app (JWT) |
| `tenant_owners` | Phone numbers authorized to talk to Modir as owner | WhatsApp owner-mode routing |

The ten tables, grouped:

```
── Identity & Access ──
tenants            (id, name, whatsapp_number[unique], plan_tier, is_active, created_at)
tenant_owners      (id, tenant_id, phone_number, name, verified_at, verification_token, verification_status)
users              (id, tenant_id, email, hashed_password, role)
customers          (id, tenant_id, phone_number, display_name, first_seen_at)
audit_log          (id, tenant_id, actor_id, action, target, created_at)

── Business Profile / Knowledge Base ──
business_profile   (tenant_id PK, business_name, description, location,
                    delivery_radius_km, accepts_delivery, accepts_pickup, logo_url)
products           (id, tenant_id, name_ar, name_en, description_ar, price_lbp,
                    price_usd, unit, category, is_available, image_url)
operating_hours    (id, tenant_id, day_of_week, open_time, close_time, is_closed, note_ar)
business_policies  (id, tenant_id, key, value)
knowledge_base_docs(id, tenant_id, source_type, source_id, content_hash,
                    embedded_at, embedding_status)
```

There is **ONE** `products` table. Phase 4 (inventory), Phase 5 (OCR), and
Phase 6 (ML) all reference this one — never a new table.

---

## Phase 1 — Tasks Overview

| Task | What | Branch |
|------|------|--------|
| 1.1 | JWT settings + Vault signing secret | `feature/MOD-1-jwt-config` |
| 1.2 | Identity & Access models (tenants, tenant_owners, users, customers, audit_log) | `feature/MOD-1-identity-models` |
| 1.3 | Business-profile & KB models (business_profile, products, operating_hours, business_policies, knowledge_base_docs) | `feature/MOD-1-profile-models` |
| 1.4 | Migration for all ten tables | `feature/MOD-1-migration` |
| 1.5 | Repository base class — forces `tenant_id` | `feature/MOD-1-repo-base` |
| 1.6 | Concrete repositories (one per table) | `feature/MOD-1-repositories` |
| 1.7 | Password hashing + JWT encode/decode | `feature/MOD-1-auth-core` |
| 1.8 | Auth endpoints (signup, login) + `get_current_user` | `feature/MOD-1-auth-api` |
| 1.9 | Tenant signup flow (business + owner + blank profile) | `feature/MOD-1-tenant-signup` |
| 1.10 | Identity resolver + `resolve_message_identity` | `feature/MOD-1-identity-resolver` |
| 1.11 | Owner-phone verification flow | `feature/MOD-1-owner-verification` |
| 1.12 | Business-profile CRUD + KB tracking on write | `feature/MOD-1-profile-crud` |
| 1.13 | Audit logging service | `feature/MOD-1-audit-log` |
| 1.14 | Two-tenant fixture + cross-tenant isolation tests | `feature/MOD-1-isolation-tests` |
| 1.15 | Identity-resolver & role tests | `feature/MOD-1-identity-tests` |
| 1.16 | CI: add pytest + tenant-scoping lint gate | `chore/MOD-1-ci-tests` |

Each task is a separate branch and PR. No exceptions. Pause for approval after each.

---

## Task 1.1 — JWT Settings + Vault Signing Secret

**Branch:** `feature/MOD-1-jwt-config`

The JWT signing secret is a secret — it lives in **Vault**, never `.env`. JWT
expiry is short (15 min) per the DoD.

**Edit `backend/app/infra/settings.py`** — add to the `Settings` class (after the LLM key block):

```python
    # JWT auth — secret RESOLVED FROM VAULT, not env. Placeholder here.
    jwt_secret: SecretStr = Field(default=SecretStr("from-vault"))
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiry_minutes: int = Field(default=15)
    # Refresh tokens are documented but not implemented in Phase 1 (see DoD).
    jwt_refresh_expiry_minutes: int = Field(default=60 * 24 * 7)
```

**Edit `backend/app/infra/vault.py`** — add the JWT secret to `secrets_map` in `resolve_secrets`:

```python
    secrets_map = {
        "gemini_api_key": ("modir/llm", "gemini_api_key"),
        "minio_access_key": ("modir/minio", "access_key"),
        "minio_secret_key": ("modir/minio", "secret_key"),
        "jwt_secret": ("modir/auth", "jwt_secret"),
    }
```

**Edit `backend/scripts/seed_vault.sh`** — add a line that seeds the JWT secret:

```bash
vault kv put secret/modir/auth jwt_secret="dev-jwt-secret-rotate-before-prod"
```

**Commit message:**
```
feat(auth): add JWT settings resolved from Vault

JWT signing secret lives in Vault (secret/modir/auth), never .env.
Expiry is 15 min; refresh-token TTL documented but not yet implemented.
```

**Verification:**
- `grep -rn "jwt_secret" backend/app/` shows it only in `settings.py` and `vault.py`
- `./backend/scripts/seed_vault.sh` then restart api → `vault.secrets.resolved count=4` in the log
- App refuses to start if `secret/modir/auth` is missing (clear error)

---

## Task 1.2 — Identity & Access Models

**Branch:** `feature/MOD-1-identity-models`

Every model inherits `Base` (giving `id`, `created_at`, `updated_at`) and adds a
**non-nullable, indexed** `tenant_id`. The Wall starts at the schema.

> Note on `created_at`: `Base` already provides it. The roadmap lists
> `created_at`/`first_seen_at` on some tables — use `Base.created_at` where it
> matches, and add `first_seen_at`/`verified_at` as explicit columns where the
> semantics differ.

**File: `backend/app/db/models.py`** (append below `Base`)

```python
from sqlalchemy import (
    Boolean,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship


class Tenant(Base):
    """A business on the platform. The root of every tenant-scoped query."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The business's WhatsApp Business number — the 'to' on every inbound message.
    # Unique across the platform: two tenants cannot share a destination number.
    whatsapp_number: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
    plan_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TenantOwner(Base):
    """A phone number authorized to talk to Modir as the owner over WhatsApp.

    Distinct from `users` (dashboard logins). A phone here gets owner-level agent
    access — adding one is a privilege escalation and MUST go through verification.
    """

    __tablename__ = "tenant_owners"
    __table_args__ = (
        # Same phone can own different tenants, but only once per tenant.
        UniqueConstraint("tenant_id", "phone_number", name="uq_owner_tenant_phone"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "pending" | "verified" — an owner phone is only trusted once verified.
    verification_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )


class User(Base):
    """Dashboard account (email + password). Logs into the React app via JWT.

    Related to TenantOwner but separate — an accountant may have a dashboard
    login with no WhatsApp owner authority.
    """

    __tablename__ = "users"
    __table_args__ = (
        # Email is unique within a tenant, not globally — two tenants may both
        # have an "owner@shop.com".
        UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="owner")


class Customer(Base):
    """Auto-created on first WhatsApp message from an unknown number.

    Scoped to a tenant: the same phone messaging two tenants is two customers.
    """

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone_number", name="uq_customer_tenant_phone"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    """Every auth event, privilege change, and owner-phone/product change."""

    __tablename__ = "audit_log"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    # Who performed the action (a user id or tenant_owner id). Nullable for
    # signup, where the actor does not yet exist.
    actor_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
```

**Commit message:**
```
feat(models): identity & access tables with tenant_id

tenants, tenant_owners, users, customers, audit_log. Every row carries a
non-nullable indexed tenant_id. whatsapp_number is unique platform-wide;
email and owner phone are unique per tenant. tenant_owners is separate from
users by design.
```

**Verification:**
- `uv run python -c "from app.db.models import Tenant, TenantOwner, User, Customer, AuditLog"` imports clean
- Each new model has a `tenant_id` column that is `nullable=False, index=True` (except `Tenant` itself, which IS the tenant)
- `whatsapp_number` carries `unique=True`

---

## Task 1.3 — Business-Profile & Knowledge-Base Models

**Branch:** `feature/MOD-1-profile-models`

These ship in Phase 1 because `products` is referenced by Phases 2/4/5/6. Define
it once, here.

**File: `backend/app/db/models.py`** (append)

```python
from sqlalchemy import Integer, Numeric, Time


class BusinessProfile(Base):
    """The shop's public identity. One row per tenant (tenant_id is the key)."""

    __tablename__ = "business_profile"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_profile_tenant"),)

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_radius_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accepts_delivery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accepts_pickup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    logo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class Product(Base):
    """THE product catalog. Reused by Phase 2 (orders), 4 (inventory),
    5 (OCR mapping), 6 (ML forecasting). One table — never a per-phase copy."""

    __tablename__ = "products"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    name_ar: Mapped[str] = mapped_column(String(255), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description_ar: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_lbp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_usd: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class OperatingHours(Base):
    """When the shop is open, per day of week (0=Mon ... 6=Sun)."""

    __tablename__ = "operating_hours"
    __table_args__ = (
        UniqueConstraint("tenant_id", "day_of_week", name="uq_hours_tenant_day"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    open_time: Mapped[Time | None] = mapped_column(Time, nullable=True)
    close_time: Mapped[Time | None] = mapped_column(Time, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Lebanese Arabic exception note, e.g. "مغلق خلال رمضان بعد الإفطار".
    note_ar: Mapped[str | None] = mapped_column(Text, nullable=True)


class BusinessPolicy(Base):
    """Key/value store for shop rules: min_order_lbp, delivery_fee_lbp, etc."""

    __tablename__ = "business_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_policy_tenant_key"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeBaseDoc(Base):
    """Tracks what is embedded in pgvector and whether it is in sync.

    source_type: "product" | "policy" | "faq" | "operating_hours"
    embedding_status: "pending" | "embedded" | "stale"
    Phase 5 processes these; Phase 1 only creates/updates the tracking rows.
    """

    __tablename__ = "knowledge_base_docs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_type", "source_id", name="uq_kb_tenant_source"
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
```

**Commit message:**
```
feat(models): business profile & knowledge-base tables

business_profile, products, operating_hours, business_policies,
knowledge_base_docs. products is the single catalog reused by phases 2/4/5/6.
knowledge_base_docs tracks pgvector sync state (pending/embedded/stale).
```

**Verification:**
- All five models import cleanly and carry indexed non-nullable `tenant_id`
- `KnowledgeBaseDoc.embedding_status` defaults to `"pending"`
- `products` has exactly one definition in the codebase (`grep -rn "__tablename__ = \"products\"" backend/`)

---

## Task 1.4 — Migration for All Ten Tables

**Branch:** `feature/MOD-1-migration`

`alembic/env.py` already targets `Base.metadata`, so autogenerate sees every
model once it's imported. Verify the import path first.

**Generate the migration:**
```bash
cd backend
uv run alembic revision --autogenerate -m "phase1 identity and profile tables"
```

**Review the generated file by hand** before committing. Confirm:
- All ten `create_table` calls are present.
- Every `tenant_id` column is `nullable=False` and has an index (`op.create_index`).
- The unique constraints exist: `tenants.whatsapp_number`, `uq_user_tenant_email`, `uq_customer_tenant_phone`, `uq_owner_tenant_phone`, `uq_profile_tenant`, `uq_hours_tenant_day`, `uq_policy_tenant_key`, `uq_kb_tenant_source`.
- `downgrade()` drops them in reverse order.

**Apply and round-trip test:**
```bash
docker compose up -d db vault
uv run alembic upgrade head
uv run alembic downgrade -1   # confirm downgrade works
uv run alembic upgrade head
```

**Commit message:**
```
feat(db): migration for the ten Phase 1 tables

Autogenerated then reviewed. Every tenant_id is non-nullable and indexed.
Unique constraints enforce whatsapp_number (global), email/phone/key
(per-tenant). upgrade/downgrade round-trips cleanly.
```

**Verification:**
- `uv run alembic upgrade head` succeeds against a fresh DB
- `uv run alembic downgrade base && uv run alembic upgrade head` round-trips
- In psql: `\d tenants` shows the unique index on `whatsapp_number`
- The `migrate` compose service runs this automatically before `api` starts

---

## Task 1.5 — Repository Base Class (The Wall, in code)

**Branch:** `feature/MOD-1-repo-base`

This is where tenant isolation is **enforced**. There is exactly ONE base class.
Every method takes `tenant_id` as a required first parameter and injects it into
every query. No method queries without it. The service layer never bypasses this
to hit raw SQL.

**File: `backend/app/repositories/base.py`**

```python
from collections.abc import Sequence
from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base

ModelT = TypeVar("ModelT", bound=Base)


class TenantScopedRepository(Generic[ModelT]):
    """Base repository. EVERY method requires tenant_id and filters by it.

    The Wall (constitution I) is enforced HERE, in code — never in a prompt and
    never trusted from a JWT claim. A model without a `tenant_id` column cannot
    be used with this base; that is intentional.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _require_tenant_scope(self, tenant_id: UUID):
        """Return the base SELECT already filtered by tenant_id.

        Subclasses build on this; they never start a query without it.
        """
        if tenant_id is None:
            # Defensive: a None tenant_id is a programming error, not a query.
            raise ValueError("tenant_id is required on every repository query")
        return select(self.model).where(self.model.tenant_id == tenant_id)

    async def get(self, tenant_id: UUID, id_: UUID) -> ModelT | None:
        stmt = self._require_tenant_scope(tenant_id).where(self.model.id == id_)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, tenant_id: UUID) -> Sequence[ModelT]:
        result = await self._session.execute(self._require_tenant_scope(tenant_id))
        return result.scalars().all()

    async def add(self, tenant_id: UUID, instance: ModelT) -> ModelT:
        # Force the row's tenant_id to the caller's scope — never trust the
        # instance to carry the right one.
        instance.tenant_id = tenant_id
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def delete(self, tenant_id: UUID, id_: UUID) -> bool:
        obj = await self.get(tenant_id, id_)
        if obj is None:
            return False
        await self._session.delete(obj)
        await self._session.flush()
        return True
```

> Note: `Tenant` itself has no `tenant_id` (it IS the tenant), so it gets a small
> dedicated repository in Task 1.6, NOT this base class.

**Commit message:**
```
feat(repo): tenant-scoped repository base class

Single base class enforcing The Wall. Every method requires tenant_id and
filters by it; add() overwrites the row's tenant_id to the caller's scope.
A None tenant_id raises rather than running an unscoped query.
```

**Verification:**
- `grep -rn "class .*Repository" backend/app/repositories/` shows one base
- Every public method signature on the base has `tenant_id` as the first param
- A unit test (added in 1.14) confirms `get`/`list` never return cross-tenant rows

---

## Task 1.6 — Concrete Repositories

**Branch:** `feature/MOD-1-repositories`

One repository per table, each extending `TenantScopedRepository`. Add only the
extra query methods each table needs. `Tenant` gets its own non-scoped repo
because it is the root.

**File: `backend/app/repositories/tenants.py`**
```python
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant


class TenantRepository:
    """Root repository. Tenant has no tenant_id — it IS the tenant.

    Lookups here are deliberately NOT tenant-scoped (there is no outer scope),
    but they are narrow: by id, or by the destination whatsapp_number.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, id_: UUID) -> Tenant | None:
        result = await self._session.execute(select(Tenant).where(Tenant.id == id_))
        return result.scalar_one_or_none()

    async def get_by_whatsapp_number(self, number: str) -> Tenant | None:
        result = await self._session.execute(
            select(Tenant).where(Tenant.whatsapp_number == number)
        )
        return result.scalar_one_or_none()

    async def add(self, tenant: Tenant) -> Tenant:
        self._session.add(tenant)
        await self._session.flush()
        return tenant
```

**File: `backend/app/repositories/users.py`**
```python
from sqlalchemy import select

from app.db.models import User
from app.repositories.base import TenantScopedRepository


class UserRepository(TenantScopedRepository[User]):
    model = User

    async def get_by_email(self, tenant_id, email: str) -> User | None:
        stmt = self._require_tenant_scope(tenant_id).where(User.email == email)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
```

**File: `backend/app/repositories/tenant_owners.py`**
```python
from app.db.models import TenantOwner
from app.repositories.base import TenantScopedRepository


class TenantOwnerRepository(TenantScopedRepository[TenantOwner]):
    model = TenantOwner

    async def get_by_phone(self, tenant_id, phone_number: str) -> TenantOwner | None:
        stmt = self._require_tenant_scope(tenant_id).where(
            TenantOwner.phone_number == phone_number
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
```

**File: `backend/app/repositories/customers.py`**
```python
from app.db.models import Customer
from app.repositories.base import TenantScopedRepository


class CustomerRepository(TenantScopedRepository[Customer]):
    model = Customer

    async def get_by_phone(self, tenant_id, phone_number: str) -> Customer | None:
        stmt = self._require_tenant_scope(tenant_id).where(
            Customer.phone_number == phone_number
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
```

Add equally thin repositories for `business_profile.py`, `products.py`,
`operating_hours.py`, `business_policies.py`, `knowledge_base_docs.py`,
`audit_log.py` — each extends the base, adds only what it needs
(`BusinessProfileRepository.get_for_tenant`, `KnowledgeBaseDocRepository.upsert`,
etc.).

**Commit message:**
```
feat(repo): concrete repositories for all ten tables

Each extends the tenant-scoped base and adds only its own lookups
(get_by_email, get_by_phone, etc.). TenantRepository is the one exception —
it is the root and has no outer scope.
```

**Verification:**
- Every repo except `TenantRepository` extends `TenantScopedRepository`
- No repo method on a scoped repo omits `tenant_id`
- `uv run python -c "import app.repositories"` (or importing each module) succeeds

---

## Task 1.7 — Password Hashing + JWT Encode/Decode

**Branch:** `feature/MOD-1-auth-core`

Industry-standard hand-rolled auth core (the professional default — most
production teams roll their own rather than adopt `fastapi-users`, for control
and auditability). PyJWT + passlib[bcrypt]. The signing secret comes from
**settings** (resolved from Vault at startup), never a literal.

**Add deps (uv):**
```bash
cd backend
uv add "pyjwt>=2.10.0" "passlib[bcrypt]>=1.7.4"
```

**File: `backend/app/infra/security.py`**
```python
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from passlib.context import CryptContext

from app.infra.settings import Settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(settings: Settings, *, user_id: UUID, tenant_id: UUID) -> str:
    """Sign a short-lived access token.

    The token CARRIES tenant_id as a claim, but the database query still filters
    by tenant_id independently — the claim is never the trust anchor (Wall rule).
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expiry_minutes),
        "type": "access",
    }
    return jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def decode_access_token(settings: Settings, token: str) -> dict:
    """Decode + verify a token. Raises jwt.PyJWTError on bad/expired tokens."""
    return jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
    )
```

**Commit message:**
```
feat(auth): password hashing and JWT encode/decode

bcrypt via passlib; PyJWT signed with the Vault-resolved secret. Tokens carry
tenant_id as a claim but it is never the trust anchor — queries filter by
tenant_id independently. Access tokens expire in 15 min.
```

**Verification:**
- `grep -rn "jwt.encode\|jwt.decode" backend/app/` only appears in `security.py`
- A round-trip test: encode then decode returns the same `sub`/`tenant_id`
- An expired token raises `jwt.ExpiredSignatureError`

---

## Task 1.8 — Auth Endpoints + `get_current_user`

**Branch:** `feature/MOD-1-auth-api`

Signup/login routes and the dependency that turns a Bearer token into a loaded,
tenant-scoped `User`. **The tenant_id used for queries comes from the token but
is re-validated against the loaded user row — never trusted blindly.**

**File: `backend/app/api/deps.py`**
```python
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant, User
from app.db.session import get_db_session
from app.infra.security import decode_access_token
from app.infra.settings import Settings, get_settings
from app.repositories.tenants import TenantRepository
from app.repositories.users import UserRepository

_bearer = HTTPBearer(auto_error=True)


async def get_current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    try:
        payload = decode_access_token(settings, creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    user_id = UUID(payload["sub"])
    tenant_id = UUID(payload["tenant_id"])

    # Load the user WITHIN the claimed tenant's scope. If the claim was tampered
    # with, the scoped lookup simply returns nothing → 401. The DB is the truth.
    user = await UserRepository(db).get(tenant_id, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found for tenant")
    return user


async def get_current_tenant(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Tenant:
    tenant = await TenantRepository(db).get_by_id(user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tenant inactive or missing")
    return tenant
```

**File: `backend/app/api/auth.py`** — `POST /auth/signup` (dashboard user under
an existing tenant), `POST /auth/login` (returns access token). Validate input
with Pydantic schemas in `app/api/schemas/auth.py`. On login, look the user up
**scoped to the tenant** (resolve tenant by a signup field — see Task 1.9 for the
combined tenant+owner+user signup) and write an audit-log entry.

**Mount the routers** in `app/main.py::create_app()`:
```python
from app.api import auth

app.include_router(auth.router)
```

**Commit message:**
```
feat(api): auth endpoints and current-user dependency

POST /auth/login issues a 15-min access token; get_current_user loads the
user inside the token's claimed tenant scope and rejects a tampered tenant_id
because the scoped lookup returns nothing. get_current_tenant loads the
active tenant. Login is audit-logged.
```

**Verification:**
- Login with good creds → 200 + token; bad creds → 401
- A request with a token whose `tenant_id` was hand-edited → 401 (scoped lookup fails)
- `curl /health` still works without auth; a protected route without a token → 401

---

## Task 1.9 — Tenant Signup Flow

**Branch:** `feature/MOD-1-tenant-signup`

One transaction creates: a `Tenant` (name + whatsapp_number), at least one
`TenantOwner` (phone, status `pending`), a dashboard `User` (email + hashed
password), and a blank `BusinessProfile` row. If the whatsapp_number already
exists → 409.

**File: `backend/app/services/signup.py`** — `register_tenant(db, payload)`:
1. Check `TenantRepository.get_by_whatsapp_number` → if found, raise 409 ("هالرقم مسجّل من قبل").
2. Create `Tenant`, flush to get its id.
3. Create `User` (hash the password) scoped to the tenant.
4. Create `TenantOwner` with `verification_status="pending"` and a
   `verification_token` (Task 1.11 verifies it).
5. Create the blank `BusinessProfile(tenant_id=...)`.
6. Write an `audit_log` entry: `action="tenant.signup"`.
7. Commit once. Return the tenant + an access token for the new user.

**Endpoint:** `POST /auth/register` in `app/api/auth.py`, Pydantic-validated
(business_name, whatsapp_number, owner_phone, email, password).

**User-facing copy in Lebanese Arabic** (errors and confirmations) — put strings
in `backend/prompts/` per the constitution, not inline.

**Commit message:**
```
feat(signup): tenant registration creates tenant, owner, user, profile

One transaction: Tenant + first TenantOwner (pending verification) + dashboard
User + blank BusinessProfile + audit_log entry. Duplicate whatsapp_number
returns 409. User-facing messages in Lebanese Arabic, kept in prompts/.
```

**Verification:**
- Register two tenants with different whatsapp_numbers → both succeed
- Register a second tenant with an existing whatsapp_number → 409
- After register: a blank `business_profile` row exists for the new tenant
- The first owner phone has `verification_status="pending"`
- An `audit_log` row with `action="tenant.signup"` exists

---

## Task 1.10 — Identity Resolver

**Branch:** `feature/MOD-1-identity-resolver`

The heart of Phase 1's routing. Takes a webhook payload (`to`, `from`, `text`)
and resolves tenant + role + actor. Auto-creates the customer on first contact.

**File: `backend/app/domain/identity.py`**
```python
from dataclasses import dataclass
from typing import Literal

from app.db.models import Customer, Tenant, TenantOwner


@dataclass
class ResolvedIdentity:
    tenant: Tenant
    role: Literal["owner", "customer"]
    actor: TenantOwner | Customer
```

**File: `backend/app/services/identity_resolver.py`** — `resolve(db, to, from_, display_name)`:
1. `tenant = TenantRepository.get_by_whatsapp_number(to)`; if `None` → raise 404
   ("destination not registered"). The destination is the only thing that picks
   the tenant.
2. `owner = TenantOwnerRepository.get_by_phone(tenant.id, from_)`.
   - If found AND `verification_status == "verified"` → role `owner`, actor = owner.
   - (A `pending` owner is NOT trusted as owner — treat as customer until verified.)
3. Else → `customer = CustomerRepository.get_by_phone(tenant.id, from_)`; if
   `None`, create one (`first_seen_at=now`, `display_name`) and audit-log
   `action="customer.autocreate"`. role `customer`, actor = customer.
4. Return `ResolvedIdentity(tenant, role, actor)`.

**FastAPI dependency** in `app/api/deps.py`:
```python
async def resolve_message_identity(
    payload: WhatsAppWebhookPayload,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResolvedIdentity:
    return await IdentityResolver(db).resolve(
        to=payload.to, from_=payload.from_, display_name=payload.display_name
    )
```
(`WhatsAppWebhookPayload` is a Pydantic schema in `app/api/schemas/webhook.py`;
the actual webhook route is Phase 2 — Phase 1 ships the resolver + dependency so
Phase 2 just depends on it.)

**Commit message:**
```
feat(identity): resolve tenant + role from a webhook payload

Destination number → tenant (404 if unknown). Sender number → verified owner
or auto-created customer. Same phone to two tenants resolves to two distinct
identities because the lookup is tenant-scoped. Auto-create is audit-logged.
```

**Verification:**
- Known verified owner phone → role `owner`
- Unknown phone → role `customer`, a new `customers` row created, reused on second message
- Message to an unregistered destination number → 404
- Same `from_` to tenant A and tenant B → two different actor records, two different tenants

---

## Task 1.11 — Owner-Phone Verification Flow

**Branch:** `feature/MOD-1-owner-verification`

A phone in `tenant_owners` gets owner-level agent access — adding one unverified
is a privilege escalation. Verification must come from an **already-verified
channel** (an existing verified owner, or the dashboard user who owns the tenant).

**Flow:**
1. An authenticated dashboard user (`get_current_user`) calls
   `POST /owners` with a phone + name → creates a `TenantOwner` with
   `verification_status="pending"` and a random `verification_token`. Audit-log
   `action="owner.add_requested"`.
2. The pending owner confirms by replying with the token over WhatsApp (or the
   dashboard user confirms): `POST /owners/{id}/verify` with the token →
   sets `verified_at=now`, `verification_status="verified"`, clears the token.
   Audit-log `action="owner.verified"`.
3. Until verified, the identity resolver treats that phone as a **customer**
   (Task 1.10 step 2), so an unverified phone never gets owner tools.

**Service:** `app/services/owner_verification.py`. All methods tenant-scoped via
`TenantOwnerRepository`. Lebanese Arabic copy in `prompts/`.

**Commit message:**
```
feat(owners): owner-phone add + verification flow

Adding an owner phone creates a pending record with a verification token;
it is trusted as owner only after /owners/{id}/verify succeeds. Until then the
resolver treats it as a customer. Every step is audit-logged.
```

**Verification:**
- Add an owner phone via authenticated request → row is `pending`
- Resolver treats the pending phone as `customer`
- Verify with the correct token → `verified`; resolver now treats it as `owner`
- Verify with a wrong token → rejected, stays `pending`
- `audit_log` has `owner.add_requested` and `owner.verified` rows

---

## Task 1.12 — Business-Profile CRUD + KB Tracking on Write

**Branch:** `feature/MOD-1-profile-crud`

CRUD endpoints, all behind `get_current_tenant`, all tenant-scoped through
repositories. Every write to a product, policy, or hours record creates/updates
a `knowledge_base_docs` row with `embedding_status="pending"` (or `"stale"` on
update) — this is the hook Phase 5 consumes.

**Endpoints (`app/api/profile.py`):**
- `PUT /profile` — upsert the `BusinessProfile`
- `POST /products`, `PUT /products/{id}`, `DELETE /products/{id}`
- `PUT /operating-hours` — replace the week's hours
- `PUT /policies` — upsert key/value policies

**KB tracking** lives in the service layer (`app/services/profile.py`), not the
route: after a successful product/policy/hours write, call
`KnowledgeBaseDocRepository.mark_pending_or_stale(tenant_id, source_type,
source_id, content_hash)`:
- No existing row → insert `pending`.
- Existing row, content_hash changed → set `stale`.
- Compute `content_hash` from the embeddable text so unchanged updates don't
  needlessly re-queue.

Every mutation writes an `audit_log` entry (`product.created`, `product.updated`,
`product.deleted`, `profile.updated`, ...).

**Commit message:**
```
feat(profile): business-profile CRUD with KB sync tracking

PUT /profile, product/hours/policy CRUD — all tenant-scoped behind
get_current_tenant. Each write upserts a knowledge_base_docs row
(pending on create, stale on content change) and an audit_log entry.
```

**Verification:**
- Create → update → delete a product, all scoped to the right tenant
- After creating a product, a `knowledge_base_docs` row exists with `embedding_status="pending"`
- Updating the product's price flips its KB row to `stale`; an unchanged update does not
- Tenant A cannot GET/PUT/DELETE tenant B's product id → 404 (not 403 — the scoped lookup just misses)

---

## Task 1.13 — Audit Logging Service

**Branch:** `feature/MOD-1-audit-log`

Centralize the audit writes the earlier tasks call. One service so every event
is shaped consistently and always carries `tenant_id`.

**File: `backend/app/services/audit.py`**
```python
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog
from app.infra.logging import get_logger

log = get_logger(__name__)


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self, *, tenant_id: UUID, action: str, actor_id: UUID | None = None, target: str | None = None
    ) -> None:
        entry = AuditLog(tenant_id=tenant_id, actor_id=actor_id, action=action, target=target)
        self._session.add(entry)
        await self._session.flush()
        # Structured log mirrors the audit row; tenant_id always present (Obs. rule).
        log.info("audit", tenant_id=str(tenant_id), action=action, actor_id=str(actor_id) if actor_id else None)
```

Refactor Tasks 1.8–1.12 to call `AuditService.record(...)` instead of
hand-building `AuditLog` rows.

**Commit message:**
```
feat(audit): centralized audit logging service

One AuditService.record() shapes every auth/privilege/profile event with
tenant_id, actor, action, target — written to audit_log AND the structured
log. Signup, login, owner add/verify, and product changes route through it.
```

**Verification:**
- Login, signup, owner-add, owner-verify, and product CRUD each produce an `audit_log` row
- Every audit structured-log line carries `tenant_id`
- `grep -rn "AuditLog(" backend/app/` shows construction only inside `audit.py`

---

## Task 1.14 — Two-Tenant Fixture + Cross-Tenant Isolation Tests

**Branch:** `feature/MOD-1-isolation-tests`

The DoD-critical proof. A fixture builds two complete tenants; tests prove A
can never reach B's data — including the **failing test that tries to cross the
wall and is blocked**.

**Add test infra deps:**
```bash
cd backend
uv add --dev "pytest-asyncio>=0.24.0" "aiosqlite>=0.20.0"   # or testcontainers/pg
```
> Prefer a real Postgres for tests (testcontainers or the compose `db`) so
> pgvector and PG-specific constraints behave like prod. Document the choice.

**File: `backend/tests/conftest.py`** — fixtures:
- `db_session` — a transactional async session rolled back per test.
- `two_tenants` — creates Tenant A and Tenant B, each with: a verified owner
  phone, a dashboard user, and a 3-product catalog. Returns both tenants' ids.

**File: `backend/tests/test_tenant_isolation.py`** — assert:
1. `ProductRepository(db).list(tenant_a)` returns only A's products; zero of B's.
2. `ProductRepository(db).get(tenant_a, b_product_id)` returns `None`.
3. `UserRepository(db).get_by_email(tenant_a, b_user_email)` returns `None`.
4. **The wall-crossing test:** attempting to fetch B's product through A's scope
   yields nothing, and a direct `get(None, ...)` raises `ValueError`. Mark intent
   clearly — this test exists to PROVE the block.
5. Profile/policy/hours isolation: A's session returns zero of B's rows.
6. A JOIN across tenants (A's data joined to B's) returns zero rows.

**Commit message:**
```
test(isolation): cross-tenant wall tests + two-tenant fixture

two_tenants builds A and B with owners, users, and catalogs. Tests prove A's
scope returns zero of B's products/users/profile/policies, that a cross-tenant
get returns None, and that an unscoped query raises. The wall-crossing test
exists to confirm the block.
```

**Verification:**
- `uv run pytest backend/tests/test_tenant_isolation.py -v` is green
- Temporarily remove the `tenant_id` filter in the base repo → these tests FAIL (confirm, then revert)

---

## Task 1.15 — Identity-Resolver & Role Tests

**Branch:** `feature/MOD-1-identity-tests`

**File: `backend/tests/test_identity_resolver.py`** — using the `two_tenants` fixture:
1. Verified owner phone of A → role `owner`, actor is the `TenantOwner`.
2. Unknown phone to A → role `customer`, a `customers` row is created.
3. Same unknown phone messaging again → the SAME customer row is reused (no duplicate).
4. Message to an unregistered destination number → raises 404.
5. **Same sender phone to A and to B → two different identities** (different
   tenant, different actor record).
6. A `pending` (unverified) owner phone → resolved as `customer`, not `owner`.

**File: `backend/tests/test_auth.py`** — JWT behavior:
1. Login returns a token; decoding it yields the right `sub` and `tenant_id`.
2. A token with a hand-edited `tenant_id` is rejected by `get_current_user`.
3. An expired token (monkeypatch expiry to a negative delta) → 401.

**Commit message:**
```
test(identity): resolver role detection + JWT tamper/expiry

Owner vs customer routing, customer auto-create-then-reuse, unknown
destination → 404, same phone to two tenants → two identities, unverified
owner treated as customer. JWT tests cover tenant-claim tampering and expiry.
```

**Verification:**
- `uv run pytest backend/tests/test_identity_resolver.py backend/tests/test_auth.py -v` is green
- The "same phone, two tenants" test asserts two distinct `tenant_id`s and actor ids

---

## Task 1.16 — CI: pytest + Tenant-Scoping Lint Gate

**Branch:** `chore/MOD-1-ci-tests`

Wire the new tests into CI and add a guard that catches a repository method
written without `tenant_id`.

**Edit `.github/workflows/ci.yml`:**
- Add a Postgres (pgvector) service container for the test job.
- Add a step: `uv run pytest backend/tests -v` (after lint/format).
- Run migrations against the service DB before the tests.
- Extend the "Forbidden patterns" step with a tenant-scoping heuristic guard,
  e.g. flag any `select(` in `app/repositories/` whose statement isn't filtered —
  keep it pragmatic (a comment-documented grep that the team accepts), since a
  perfect static check is hard. At minimum: assert `TenantScopedRepository` is
  the only base and that `app/repositories/` contains no raw `text(` SQL.

**Commit message:**
```
ci(tests): run pytest with a pgvector service + scoping guard

Adds the Phase 1 test suite to CI against a real Postgres, runs migrations
first, and extends the forbidden-patterns gate to flag raw SQL in
repositories. A regression in tenant scoping now fails the build.
```

**Verification:**
- Push the branch; CI runs migrations, then the full suite, and passes
- Intentionally break tenant scoping → CI test job fails; revert → green

---

## Phase 1 — Definition of Done

Run through this before marking Phase 1 complete:

- [ ] Two test tenants exist with different WhatsApp numbers, owner phones, and product catalogs. A query in Tenant A's session returns **zero** rows from Tenant B — products, profile, AND policies.
- [ ] Every SQLAlchemy model has `tenant_id` as a non-nullable, indexed column (except `Tenant`, which is the tenant).
- [ ] Every repository method takes `tenant_id` as a required parameter (except `TenantRepository`, the root).
- [ ] Identity resolver: known verified owner phone → owner; unknown phone → customer (auto-created); message to unknown destination → 404.
- [ ] Same sender phone messaging two different tenants resolves to two different identities.
- [ ] Business-profile CRUD works: create, update, delete a product — all scoped to the right tenant.
- [ ] Updating a product creates/updates a `knowledge_base_docs` row with `embedding_status="pending"` (or `"stale"` on content change).
- [ ] JWT expiry is short (15 min); the refresh-token mechanism is documented even though not implemented.
- [ ] A failing test exists that *tries* to bypass tenant scoping and gets blocked.
- [ ] Adding a new owner phone goes through verification; an unverified phone is not treated as owner.
- [ ] Audit log captures: who logged in, when, from what tenant, every owner-phone change, every product change.
- [ ] `grep -rn "os.getenv\|print(\|import requests" backend/app/` still returns nothing.
- [ ] CI is green on `main` (lint, format, migrations, full pytest suite).

## Phase 1 — Defend-it Preparation

Practice answering these out loud (these become `docs/PHASE_1_DEFEND_IT.md`):

1. Where exactly is tenant isolation enforced? Show me the line of code.
2. A WhatsApp message arrives. Walk me through resolving its tenant AND its role, line by line.
3. What happens if a malicious user changes the `tenant_id` in their JWT payload?
4. Abu Khaled adds his wife's phone as a co-owner. What's the verification flow?
5. The same phone number messages two different Modir tenants — why are they two separate identities?
6. Abu Khaled updates a product price. Walk me through the database AND the knowledge-base tracking table.
7. What does `Authorization: Bearer ...` actually carry? What's inside the JWT?
8. When the token expires, what does the frontend do?
9. What if someone signs up with a WhatsApp number already registered to another tenant?
10. Show me the `products` table. Why does Phase 4 not create its own inventory table?

If you can't answer any of these without looking, the phase is not done.

## Ready for Phase 2?

You are ready when:
- Every checkbox above is checked.
- All 10 defend-it questions can be answered fluently, out loud, without notes.
- `uv run pytest backend/tests` is green, including the wall-crossing test.
- The identity resolver is importable and depended-upon, ready for Phase 2's
  webhook dispatcher to call `resolve_message_identity` without modification.

Phase 2 is the Customer Order Flow — the first LangGraph agent. It depends
entirely on this phase's identity resolver and tenant-scoped repositories.
Do not start it until The Wall is bulletproof.
