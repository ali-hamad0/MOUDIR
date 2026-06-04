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
