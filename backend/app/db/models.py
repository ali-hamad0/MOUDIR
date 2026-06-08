from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Embedding dimension for the vector_chunks column (Phase 5). Must match
# Settings.embedding_dim and the embedding model (text-embedding-004 = 768). Changing
# it is a migration. Kept as a module constant so the model and a future migration
# agree on one number.
EMBEDDING_DIM = 768


class Base(DeclarativeBase):
    """Base ORM class. Every model inherits from this."""

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


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
    # Provenance marker (Phase 6, Task 6.2). NULL/"real" for genuine tenants;
    # "synthetic" for tenants created by the ML history seeder. The honesty rule:
    # models trained on synthetic data must be able to say so, and anything that must
    # be real can exclude synthetic tenants. Nullable so it never burdens real signup.
    data_source: Mapped[str | None] = mapped_column(String(16), nullable=True)


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
    verification_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")


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
    # Founder-onboarding activation (Phase 1.5). When a founder approves a signup
    # request, the user is created WITHOUT a usable password and with a one-time,
    # expiring activation_token; the owner sets their own password via /activate,
    # which stamps activated_at and clears the token. A user with activated_at is
    # NULL (and only a placeholder password) cannot log in.
    activation_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    activation_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    __table_args__ = (UniqueConstraint("tenant_id", "day_of_week", name="uq_hours_tenant_day"),)

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    open_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    close_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Lebanese Arabic exception note, e.g. "مغلق خلال رمضان بعد الإفطار".
    note_ar: Mapped[str | None] = mapped_column(Text, nullable=True)


class BusinessPolicy(Base):
    """Key/value store for shop rules: min_order_lbp, delivery_fee_lbp, etc."""

    __tablename__ = "business_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_policy_tenant_key"),)

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
        UniqueConstraint("tenant_id", "source_type", "source_id", name="uq_kb_tenant_source"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")


# ── Phase 2 — Customer Order Flow ───────────────────────────────────────────


class Order(Base):
    """A confirmed customer order, scoped to a tenant + customer.

    Phase 2 writes status='confirmed'. No inventory deduction here (Phase 4),
    no ML (Phase 6). Totals are snapshots taken at confirm time.
    """

    __tablename__ = "orders"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="confirmed")
    fulfillment_type: Mapped[str] = mapped_column(String(16), nullable=False, default="pickup")
    # The customer's raw Arabic time phrase ("بكرا الصبح"); a parsed timestamp is
    # best-effort and may be null in Phase 2.
    requested_time_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_lbp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_usd: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    raw_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderItem(Base):
    """One line of an order.

    References a real products.id for THIS tenant; the product name and unit
    price are snapshotted so a later catalog edit can never rewrite a past order
    (the catalog is mutable; the order is a record).
    """

    __tablename__ = "order_items"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True
    )
    name_ar_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_lbp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_price_usd: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    line_total_lbp: Mapped[int | None] = mapped_column(Integer, nullable=True)


class OrderEvent(Base):
    """Lightweight per-order trail (created, rail_tripped, parse_retry, ...).

    The cross-cutting audit_log still records tenant-level events via
    AuditService; this table keeps order-specific breadcrumbs close to the order.
    """

    __tablename__ = "order_events"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    order_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True, index=True
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Phase 4 — Inventory & Suppliers ─────────────────────────────────────────


class Inventory(Base):
    """Live stock level for one product, one row per (tenant_id, product_id).

    The Wall (constitution I): tenant-scoped — a non-nullable, indexed tenant_id,
    and every repository method filters by it. The inventory-deduction JOIN
    (order_items → products → inventory) is scoped on every side.

    This REFERENCES the single `products` table (a nullable FK is wrong here —
    inventory exists only for a real product). It does NOT copy the catalog into a
    per-phase "inventory_products" table (ROADMAP pitfall; constitution: one
    catalog table). Inventory is the moving quantity; the product is its identity,
    price, and name — kept in the one place they already live.

    `quantity` carries a DB-level CHECK (>= 0): the schema itself refuses an
    oversell. Combined with the guarded UPDATE in the repository (Task 4.2), two
    concurrent orders for the last unit can never both succeed and the level can
    never go negative.
    """

    __tablename__ = "inventory"
    __table_args__ = (
        # One live-stock row per product per tenant.
        UniqueConstraint("tenant_id", "product_id", name="uq_inventory_tenant_product"),
        # Schema-level backstop against oversell — the database itself rejects a
        # negative quantity even if application code is wrong (ROADMAP pitfall).
        CheckConstraint("quantity >= 0", name="ck_inventory_qty_nonneg"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Low-stock trip point. Null = untracked threshold (never trips a reorder).
    reorder_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Default quantity to reorder when the threshold trips (the agent may override).
    reorder_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Who a reorder for this product goes to. Null until the owner sets it.
    supplier_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True
    )


class Supplier(Base):
    """Where a reorder is dispatched to, scoped to a tenant.

    The Wall (constitution I): non-nullable, indexed tenant_id; every repository
    method filters by it. Tenant A's inventory can never reference tenant B's
    supplier — both sides are tenant-scoped.

    dispatch_type is `webhook` for now (Phase 4): a PO for this supplier is POSTed
    to `webhook_url` via the SupplierDispatcher (Task 4.11). `contact_email` is an
    optional fallback channel.
    """

    __tablename__ = "suppliers"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dispatch_type: Mapped[str] = mapped_column(String(16), nullable=False, default="webhook")
    webhook_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# ── Phase 4 — Purchase Orders & HIL artifact ────────────────────────────────


class PurchaseOrder(Base):
    """A drafted/approved/sent reorder — the Human-in-the-Loop artifact.

    The Wall (constitution I): non-nullable, indexed tenant_id; every repository
    method filters by it. References the single `products` table and a tenant's own
    `suppliers` row — both sides tenant-scoped, never across the Wall.

    Status lifecycle (single source of truth for the gate + UI)::

        draft ──approve──► approved ──dispatch(signed token)──► sent
          │                  │                                   ▲
          │                  └─dispatch fails after retries──► dispatch_failed
          │                                                        │ manual "mark sent"
          │                                                        ▼
          └──reject──► rejected                                  (sent)

    - draft           — the agent proposed it; awaiting a human. NEVER dispatched.
    - approved        — a human approved; a signed token now exists; dispatch queued.
    - sent            — the dispatcher confirmed delivery (webhook 2xx / dev log).
    - dispatch_failed — the retry budget is exhausted; sits in the manual queue.
    - rejected        — a human declined; carries reject_reason. Provisions nothing.

    ⚠️ `status` is the lifecycle MARKER for the UI — it is NOT the security gate.
    The gate is the signed approval token verified by ActionGate (Task 4.10):
    `status == "approved"` is necessary but NOT sufficient to dispatch. A bug that
    flips this status must still not produce a send without a valid token
    (constitution V, the literal reading the owner asked for).
    """

    __tablename__ = "purchase_orders"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    # Where the reorder goes. Nullable: a draft may exist before the owner has set
    # a supplier on the product's inventory row.
    supplier_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    # Why the agent drafted it (e.g. "crossed reorder threshold: 4 <= 5").
    draft_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The Tier-1 LLM-drafted supplier note, in Lebanese Arabic (Task 4.8).
    agent_note_ar: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The approver/rejecter — a user of THIS tenant. Null until reviewed.
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Required on reject (enforced in the service/API, Task 4.7/4.12).
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Dispatch bookkeeping (Task 4.11): how many sends were attempted, when it
    # finally went out, and the last error if it failed.
    dispatch_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PurchaseOrderEvent(Base):
    """Lightweight per-PO trail (drafted, approved, rejected, sent, ...).

    Mirrors OrderEvent: the cross-cutting audit_log records tenant-level events via
    AuditService; this table keeps PO-specific breadcrumbs close to the PO. The
    purchase_order_id is nullable for the same reason OrderEvent.order_id is —
    a breadcrumb can outlive its row.
    """

    __tablename__ = "purchase_order_events"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    purchase_order_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("purchase_orders.id"), nullable=True, index=True
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Phase 7 — CustomerAgent HIL artifact ────────────────────────────────────


class PendingReengagement(Base):
    """A drafted customer re-engagement message queued for HIL approval.

    The Wall (constitution I): non-nullable, indexed tenant_id; every repository
    method filters by it. References the tenant's own `customers` row.

    Status lifecycle (single source of truth for the owner's inbox)::

        draft ──approve (Phase 10)──► approved ──send (Meta API, Phase 10)──► sent
          │
          └──reject──► rejected

    - draft    — the agent proposed it; awaiting human review. NEVER sent.
    - approved — the owner approved; Phase 10 will send via the Meta API.
    - rejected — the owner declined.
    - sent     — Phase 10: message delivered to the customer (not Phase 7).

    ⚠️ Like PurchaseOrder, `status` is the lifecycle MARKER for the UI — it is
    NOT the security gate. Sending is Phase 10 and is gated by a signed approval
    token (the same ActionGate). A status flip must never trigger a send without
    a valid token (constitution V).

    The `action_key` (`"send_reengagement:{tenant_id}:{customer_id}"`) is also
    the idempotency key: the tool checks for an existing draft before creating
    a new one, preventing duplicate queuing.
    """

    __tablename__ = "pending_reengagements"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False, index=True
    )
    # Snapshot of the customer's display name at draft time (for inbox display).
    customer_name_ar: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The LLM-drafted Lebanese Arabic re-engagement message (not yet sent).
    draft_message_ar: Mapped[str] = mapped_column(Text, nullable=False)
    # Idempotency key: "send_reengagement:{tenant_id}:{customer_id}".
    # Also the action string for ActionGate.authorize in Phase 10.
    action_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # "draft" | "approved" | "rejected" | "sent" (sent is Phase 10)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")


# ── Phase 5 — Supplier bills (OCR artifact) ─────────────────────────────────


class SupplierBill(Base):
    """A photographed paper supplier bill and its OCR lifecycle (the OCR artifact).

    The Wall (constitution I): non-nullable, indexed tenant_id; every repository
    method filters by it. The image itself lives in MinIO under a tenant-prefixed
    key (`object_key`, app/infra/storage.py) — `object_key` here is just the
    reference; the bytes never sit in the database. References the tenant's own
    `suppliers` row (nullable — the supplier may be unknown until extraction).

    Status lifecycle (single source of truth for the gate + UI)::

        uploaded ──worker picks up──► ocr_processing ──OCR+extract ok──► extracted
                       │                                                    │
                       └──OCR/extract fails──► ocr_failed                   │
                                                                           │ human reviews
                       committed ◄──approve(signed bill.commit token)──────┤
                                                                           │
                       rejected  ◄──reject(reason)─────────────────────────┘

    - uploaded       — the file is in MinIO; the worker has not started. NO stock change.
    - ocr_processing — the worker is running OCR + extraction.
    - extracted      — BillData is ready; the draft awaiting a human. NO stock change.
    - ocr_failed     — OCR/extraction failed; surfaced for retry / manual entry.
    - committed      — a human approved; a signed bill.commit token cleared the gate
                       and every validated line increased stock. Terminal.
    - rejected       — a human declined; carries reject_reason. Image stays in MinIO.

    ⚠️ Like the PurchaseOrder, `status` is the lifecycle MARKER for the UI — it is
    NOT the security gate. Committing a bill to stock is a Level-2 action gated by a
    signed `bill.commit` token verified by ActionGate (Task 5.11), the SAME gate as
    the purchase-order dispatch (a new action string, not a new gate). A bug that
    flips `status` to "committed" must still not move stock without a valid token
    (constitution V).
    """

    __tablename__ = "supplier_bills"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    # The supplier the bill is from. Nullable: unknown until extraction maps it (and
    # the owner may never map it). Tenant-scoped — never another tenant's supplier.
    supplier_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True
    )
    # The MinIO object key (tenant-prefixed). The reference, not the bytes.
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded")
    # Which OCR engine produced the text (stub | cloud_vision | tesseract) — recorded
    # for audit/repro since accuracy differs by engine.
    ocr_engine: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The raw OCR output, kept so a bill can be re-extracted without re-OCR'ing.
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The validated BillData structure (the extraction agent's output). JSONB so the
    # whole structured result is queryable without a column per field.
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Parsed from the bill (best-effort; may be null on a low-confidence extraction).
    bill_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # The currency as printed on the bill ("LBP" | "USD").
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # The lowest per-field confidence on the bill — the review signal that tells the
    # UI which bills need closer attention (Task 5.6). NOT an auto-commit switch:
    # every bill goes to a human in Phase 5.
    min_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    # The approver/rejecter — a user of THIS tenant. Null until reviewed.
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Required on reject (enforced in the service/API, Task 5.3/5.12).
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupplierBillLine(Base):
    """One extracted line of a supplier bill, mapped (or not) to a product.

    The Wall (constitution I): non-nullable, indexed tenant_id; every repository
    method filters by it. References the ONE `products` table for its mapping target
    (nullable: a line is unmapped until the owner maps it in review, and an unmapped
    line can never commit to stock — Task 5.11). It does NOT copy the catalog.

    `committed` records whether THIS line applied to stock when the bill was
    committed, so a partially-actioned bill (some lines mapped, some not) is
    auditable line by line.
    """

    __tablename__ = "supplier_bill_lines"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    supplier_bill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("supplier_bills.id"), nullable=False, index=True
    )
    # The OCR'd line exactly as read — kept so the owner can compare against the
    # parsed fields in the review screen.
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The extracted item name (Lebanese Arabic). Drives the product mapping.
    name_ar: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    unit_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    line_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Per-line confidence (0..1) — flags the line for review when low (Task 5.6).
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    # The mapped catalog target for THIS tenant. Null = unmapped (cannot commit).
    product_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("products.id"), nullable=True, index=True
    )
    # Did this line apply to stock on commit? Stays False for unmapped/skipped lines.
    committed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SupplierBillEvent(Base):
    """Lightweight per-bill trail (uploaded, ocr_processing, extracted, committed...).

    Mirrors OrderEvent / PurchaseOrderEvent: the cross-cutting audit_log records
    tenant-level events via AuditService; this table keeps bill-specific breadcrumbs
    close to the bill. `supplier_bill_id` is nullable for the same reason
    OrderEvent.order_id is — a breadcrumb can outlive its row.
    """

    __tablename__ = "supplier_bill_events"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    supplier_bill_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("supplier_bills.id"), nullable=True, index=True
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Phase 5 — RAG vector store (pgvector) ───────────────────────────────────


class VectorChunk(Base):
    """An embedded chunk of tenant content for RAG retrieval (pgvector).

    The Wall (constitution I): non-nullable, indexed tenant_id; EVERY retrieval
    filters by tenant_id BEFORE the similarity order-by (constitution I, literal —
    never filter after similarity). Two corpora share this table, distinguished by
    `corpus`:
      - "knowledge" — products, policies, hours (from knowledge_base_docs)
      - "bills"     — committed supplier bills (historical, for Phase 6 context)

    `source_type`/`source_id` point back at the row the chunk came from (e.g.
    ("product", product_id)); `content_hash` lets the worker skip re-embedding
    unchanged content; `chunk_index` orders the chunks of one source. The embedding
    is a fixed-width pgvector column — its dimension MUST match Settings.embedding_dim
    and the embedding model (changing it is a migration).
    """

    __tablename__ = "vector_chunks"
    __table_args__ = (
        # One chunk row per (tenant, corpus, source, chunk index). Re-embedding a
        # source replaces its chunks, so this stays unique.
        UniqueConstraint(
            "tenant_id",
            "corpus",
            "source_type",
            "source_id",
            "chunk_index",
            name="uq_vector_chunk_source",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    corpus: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)


class Admin(Base):
    """The Modir founder / super-admin — an identity that sits ABOVE all tenants.

    Deliberately separate from `users` (which are strictly tenant-bound): keeping
    the founder out of `users` means every tenant-scoped query can assume its
    user belongs to exactly one tenant, and there is no "null-tenant user" to
    reason about. The founder is the ONE identity allowed to act across tenants,
    and ONLY through dedicated, audited admin endpoints — never through a
    tenant-scoped repository (constitution I). See get_current_admin.
    """

    __tablename__ = "admins"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SignupRequest(Base):
    """A prospective owner's application to join Modir (Phase 1.5, founder-gated
    onboarding).

    Deliberately NOT tenant-scoped: a pending request has no tenant until a
    founder approves it. This table sits ABOVE the tenant boundary — by design,
    not a hole in The Wall (constitution I). A request never reads or writes
    tenant data; on approval the founder provisions a tenant via register_tenant
    and records its id in `provisioned_tenant_id`. `created_at` (from Base) is the
    requested-at time.

    status: "pending" | "approved" | "rejected"
    """

    __tablename__ = "signup_requests"

    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    # Which founder reviewed it and when, plus the reason when status="rejected".
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("admins.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Payment is handled out-of-band for now (Phase 1.5); the founder may stamp
    # this when they confirm payment before approving.
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The tenant created when this request was approved (null until then).
    provisioned_tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True
    )
