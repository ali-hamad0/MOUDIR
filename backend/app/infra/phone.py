"""Lebanese phone-number normalization (registration phone verification).

A small, dependency-free validator: we deliberately do NOT pull in
`phonenumbers` (a heavy lib, and Docker DNS is blocked in-container for image
builds — see CLAUDE memory). Modir only ever needs Lebanese numbers, so a
focused normalizer is both lighter and stricter than a generic library here.

`normalize_lebanese_mobile` returns the number in E.164 (+961XXXXXXXX) or None
if it is not a plausible Lebanese mobile. Mobile only: the signup OTP is
delivered over WhatsApp, which a landline cannot receive.
"""

from __future__ import annotations

import re

# Lebanese mobile prefixes (national, leading 0 dropped in E.164). Each is
# followed by exactly 6 subscriber digits → an 8-digit national number.
#   03            — legacy MIC1/MIC2
#   70 71 76 78 79 — touch / alfa ranges
#   81            — newer range
_MOBILE_PREFIXES = ("3", "70", "71", "76", "78", "79", "81")

# Strip everything that isn't a digit; we re-derive the country code below.
_NON_DIGIT = re.compile(r"\D")


def normalize_lebanese_mobile(raw: str) -> str | None:
    """Return a Lebanese mobile number as +961XXXXXXXX, or None if invalid.

    Accepts the shapes a human actually types or pastes from WhatsApp:
    "03 234 567", "+961 3 234567", "009613234567", "70123456", "0096170123456".
    Bidi marks, spaces, and dashes are discarded before parsing.
    """
    if not raw:
        return None

    digits = _NON_DIGIT.sub("", raw)
    if not digits:
        return None

    # Reduce any international wrapper to the national significant number.
    if digits.startswith("00961"):
        national = digits[5:]
    elif digits.startswith("961"):
        national = digits[3:]
    elif digits.startswith("0"):
        national = digits[1:]  # local trunk "0" — e.g. 03..., 070...
    else:
        national = digits

    # A national mobile number is prefix (1–2 digits) + 6 subscriber digits.
    for prefix in _MOBILE_PREFIXES:
        if national.startswith(prefix) and len(national) == len(prefix) + 6:
            return f"+961{national}"

    return None
