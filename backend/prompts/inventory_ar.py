"""User-facing inventory / supplier messages in Lebanese Arabic.

Per the constitution, user-facing text lives in prompts/ — never inline.
"""

# Product id not found within this shop's scope (cross-tenant or deleted) when
# the owner tries to set its inventory level.
PRODUCT_NOT_FOUND = "ما لقينا هالمنتج. تأكّد منه وجرّب مرة تانية."

# Supplier id not found within this shop's scope when updating it.
SUPPLIER_NOT_FOUND = "ما لقينا هالمورّد. تأكّد منه وجرّب مرة تانية."

# Order id not found within this shop's scope when marking it complete.
ORDER_NOT_FOUND = "ما لقينا هالطلب. تأكّد منه وجرّب مرة تانية."

# The order is not in `confirmed` status (e.g. already completed) so it can't be
# completed again.
ORDER_NOT_COMPLETABLE = "هالطلب مش جاهز ليخلّص. يمكن يكون مخلّص من قبل."

# A line needs more units than there is in stock — the whole completion is rolled
# back, nothing was deducted.
INSUFFICIENT_STOCK = "ما في كمية كافية بالمخزون لهالطلب. عدّل المخزون وجرّب مرة تانية."

# ── Approvals inbox (Task 4.12) ──────────────────────────────────────────────

# The purchase order id is not this shop's (cross-tenant or deleted) when the
# owner tries to approve / reject / mark it sent.
PO_NOT_FOUND = "ما لقينا طلب الشرا هيدا. تأكّد منه وجرّب مرة تانية."

# The PO is in a status that doesn't allow the requested action (e.g. approving a
# PO that's already approved, rejecting a sent one).
PO_INVALID_TRANSITION = "ما فينا نعمل هالعملية على طلب الشرا بحالته الحالية."

# Rejecting a draft PO requires a reason (enforced at the schema + the service).
PO_REJECT_REASON_REQUIRED = "لازم تكتب سبب الرفض."
