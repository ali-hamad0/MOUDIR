"""Lebanese-Arabic copy for the supplier-bill review API (Phase 5, Task 5.12).

Per the constitution, user-facing strings live in prompts/ — never inline in the
route code. These are the owner-facing error messages the bill-review endpoints
return (mirrors prompts/inventory_ar.py for the PO inbox).
"""

# The bill id is not this tenant's (scoped lookup missed) or doesn't exist → 404.
BILL_NOT_FOUND = "ما لقينا هالفاتورة. تأكّد منها وجرّب مرة تانية."

# A transition was requested from a status that doesn't allow it (e.g. approving a
# bill that's not under review, editing a committed bill) → 409.
BILL_INVALID_TRANSITION = "ما فينا نعمل هالعملية على الفاتورة بحالتها الحالية."

# Approve was attempted while a line with a quantity is still not mapped to a
# product → 422. The owner must map every quantitied line before approving.
BILL_LINES_NOT_MAPPED = "في بنود بالفاتورة لسا ما مربوطة بمنتج. اربطهن قبل ما توافق."
