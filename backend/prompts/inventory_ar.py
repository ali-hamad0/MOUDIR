"""User-facing inventory / supplier messages in Lebanese Arabic.

Per the constitution, user-facing text lives in prompts/ — never inline.
"""

# Product id not found within this shop's scope (cross-tenant or deleted) when
# the owner tries to set its inventory level.
PRODUCT_NOT_FOUND = "ما لقينا هالمنتج. تأكّد منه وجرّب مرة تانية."

# Supplier id not found within this shop's scope when updating it.
SUPPLIER_NOT_FOUND = "ما لقينا هالمورّد. تأكّد منه وجرّب مرة تانية."
