"""Build embeddable text from a knowledge-base source row (Phase 5, Task 5.15).

The embedding worker drains `knowledge_base_docs` rows (source_type ∈ {product,
policy, operating_hours}); for each it loads the source and turns it into a short
Lebanese-Arabic text the embedder can vectorize, then chunks it. The text is built so
a customer's natural question ("بتوصّلوا لبيروت؟", "شو أوقات الدوام؟") lands near the
right chunk.

Pure-ish: `build_kb_text` reads via the tenant-scoped repos (the Wall holds), returns
a string or None (the source vanished — the worker then drops the chunks).
`chunk_text` is a documented, dependency-free splitter; KB content is short so it is
usually one chunk, but long product descriptions split on paragraph/size.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BusinessPolicy, OperatingHours, Product, SupplierBill
from app.repositories.business_policies import BusinessPolicyRepository
from app.repositories.operating_hours import OperatingHoursRepository
from app.repositories.products import ProductRepository
from app.repositories.supplier_bills import SupplierBillRepository

# Day-of-week labels (0=Mon .. 6=Sun) in Lebanese Arabic, for hours text.
_DAYS_AR = ["الإثنين", "الثلاثا", "الأربعا", "الخميس", "الجمعة", "السبت", "الأحد"]

# Soft cap on a single chunk (characters). KB content is short; this only splits a
# long product description. Documented so retrieval behaviour is predictable.
_CHUNK_CHARS = 800


def _product_text(p: Product) -> str:
    parts = [f"المنتج: {p.name_ar}"]
    if p.name_en:
        parts.append(f"(English: {p.name_en})")
    if p.description_ar:
        parts.append(p.description_ar)
    if p.category:
        parts.append(f"الفئة: {p.category}")
    if p.price_lbp is not None:
        parts.append(f"السعر: {p.price_lbp} ل.ل")
    if p.unit:
        parts.append(f"الوحدة: {p.unit}")
    parts.append("متوفر" if p.is_available else "غير متوفر حالياً")
    return " — ".join(parts)


def _policy_text(p: BusinessPolicy) -> str:
    # The key/value policy store; surface both so a question about "توصيل"/"دفع"
    # retrieves the right rule.
    return f"سياسة المحل — {p.key}: {p.value or ''}".strip()


async def _bill_text(session: AsyncSession, tenant_id: UUID, bill: SupplierBill) -> str:
    """A historical supplier bill, rendered from its lines, for the `bills` corpus
    (Phase 6 forecasting context). Supplier + date + the item lines."""
    parts = ["فاتورة مورّد"]
    if bill.bill_date:
        parts.append(f"بتاريخ {bill.bill_date.isoformat()}")
    rows = await SupplierBillRepository(session).get_lines(tenant_id, bill.id)
    for line, _product in rows:
        if line.name_ar:
            qty = f" ({line.quantity})" if line.quantity is not None else ""
            parts.append(f"{line.name_ar}{qty}")
    if bill.total_amount is not None:
        parts.append(f"المجموع {bill.total_amount} {bill.currency or ''}".strip())
    return " — ".join(parts)


def _hours_text(h: OperatingHours) -> str:
    day = _DAYS_AR[h.day_of_week] if 0 <= h.day_of_week < 7 else str(h.day_of_week)
    if h.is_closed:
        body = "مسكّر"
    elif h.open_time and h.close_time:
        body = f"من {h.open_time.strftime('%H:%M')} لـ{h.close_time.strftime('%H:%M')}"
    else:
        body = "أوقات الدوام غير محددة"
    text = f"أوقات الدوام يوم {day}: {body}"
    if h.note_ar:
        text += f" ({h.note_ar})"
    return text


async def build_kb_text(
    session: AsyncSession, tenant_id: UUID, source_type: str, source_id: UUID
) -> str | None:
    """Load the source row (tenant-scoped) and render it to embeddable text.

    Returns None if the source no longer exists (deleted between queue and embed) so
    the worker drops any prior chunks and marks the doc handled. An unknown
    source_type also returns None (defensive)."""
    if source_type == "product":
        product = await ProductRepository(session).get(tenant_id, source_id)
        return _product_text(product) if product is not None else None
    if source_type == "policy":
        policy = await BusinessPolicyRepository(session).get(tenant_id, source_id)
        return _policy_text(policy) if policy is not None else None
    if source_type == "operating_hours":
        hours = await OperatingHoursRepository(session).get(tenant_id, source_id)
        return _hours_text(hours) if hours is not None else None
    if source_type == "bill":
        bill = await SupplierBillRepository(session).get(tenant_id, source_id)
        return await _bill_text(session, tenant_id, bill) if bill is not None else None
    return None


def chunk_text(text: str) -> list[str]:
    """Split text into chunks no larger than ~_CHUNK_CHARS, on paragraph boundaries.

    KB content is short, so this almost always returns a single chunk; a long product
    description splits on blank lines, then hard-splits any oversized piece. Empty
    input yields no chunks.
    """
    text = text.strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: list[str] = []
    for para in paragraphs:
        if len(para) <= _CHUNK_CHARS:
            chunks.append(para)
            continue
        # Hard-split an oversized paragraph into _CHUNK_CHARS-sized pieces.
        for i in range(0, len(para), _CHUNK_CHARS):
            chunks.append(para[i : i + _CHUNK_CHARS])
    return chunks
