"""User-facing owner-management messages in Lebanese Arabic.

Per the constitution, user-facing text lives in prompts/ — never inline.
"""

# Tried to add a phone that already exists as an owner for this shop.
OWNER_ALREADY_EXISTS = "هالرقم مضاف من قبل كصاحب محل."

# /owners/{id}/verify with a wrong or expired token.
INVALID_VERIFICATION_TOKEN = "رمز التأكيد غلط. تأكّد منه وجرّب مرة تانية."

# The owner id is not found within this shop's scope.
OWNER_NOT_FOUND = "ما لقينا هالصاحب. تأكّد من الرقم."

# Owner is already verified — nothing to do.
OWNER_ALREADY_VERIFIED = "هالصاحب مأكّد من قبل."
