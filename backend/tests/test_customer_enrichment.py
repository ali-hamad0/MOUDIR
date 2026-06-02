"""Task 2.12 — customer name enrichment.

Heuristic extraction fires on a clear self-introduction and rejects order text.
The service updates display_name tenant-scoped, audit-logs the change, and is a
no-op when no name is stated or it is unchanged.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, Customer, Tenant
from app.services.customer_enrichment import CustomerEnrichmentService, extract_name


@pytest.mark.parametrize(
    "text,expected",
    [
        ("اسمي سارة", "سارة"),
        ("أنا أبو خالد", "أبو خالد"),
        ("انا ربيع منصور", "ربيع منصور"),
        ("معك جورج", "جورج"),
        # Name + order in one message: truncate at the order verb.
        ("اسمي ربيع بدي ٥ كعكات", "ربيع"),
        ("أنا سامي ومعك بدي توصيل", "سامي"),
    ],
)
def test_extract_name_fires_on_introduction(text, expected):
    assert extract_name(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "أنا بدي ٥ كعكات",  # order, not an introduction
        "بدي منقوشة",
        "مرحبا",
        "",
        None,
    ],
)
def test_extract_name_ignores_non_introductions(text):
    assert extract_name(text) is None


async def _customer(db: AsyncSession, display_name=None):
    ta = Tenant(name="A", whatsapp_number="+961ENRA")
    db.add(ta)
    await db.flush()
    cust = Customer(tenant_id=ta.id, phone_number="+96170ENR", display_name=display_name)
    db.add(cust)
    await db.flush()
    return ta, cust


async def test_enrich_updates_display_name_and_audits(db_session: AsyncSession):
    ta, cust = await _customer(db_session, display_name=None)
    result = await CustomerEnrichmentService(db_session).enrich_from_message(
        tenant_id=ta.id, customer=cust, text="اسمي سارة"
    )
    assert result == "سارة"
    refreshed = (
        await db_session.execute(select(Customer).where(Customer.id == cust.id))
    ).scalar_one()
    assert refreshed.display_name == "سارة"
    audited = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.tenant_id == ta.id, AuditLog.action == "customer.name_updated")
        )
    ).scalar()
    assert audited == 1


async def test_enrich_noop_when_no_name(db_session: AsyncSession):
    ta, cust = await _customer(db_session, display_name="قديم")
    result = await CustomerEnrichmentService(db_session).enrich_from_message(
        tenant_id=ta.id, customer=cust, text="بدي ٥ كعكات"
    )
    assert result is None
    refreshed = (
        await db_session.execute(select(Customer).where(Customer.id == cust.id))
    ).scalar_one()
    assert refreshed.display_name == "قديم"  # unchanged


async def test_enrich_noop_when_same_name(db_session: AsyncSession):
    ta, cust = await _customer(db_session, display_name="سارة")
    result = await CustomerEnrichmentService(db_session).enrich_from_message(
        tenant_id=ta.id, customer=cust, text="اسمي سارة"
    )
    assert result is None  # already that name → no update, no audit churn
