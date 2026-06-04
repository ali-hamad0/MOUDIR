"""Founder-onboarding activation messages in Lebanese Arabic (Phase 1.5).

Per the constitution, user-facing text lives in prompts/ — never inline. These
reach the owner: the activation email and the set-password responses.
"""

# Activation email (sent after the founder approves a signup request).
ACTIVATION_EMAIL_SUBJECT = "فعّل حسابك بمودير"
# {link} is replaced with the one-time activation URL.
ACTIVATION_EMAIL_BODY = (
    "أهلين!\n\n"
    "تمت الموافقة على محلك بمودير. إضغط هالرابط تحت تحط كلمة السر وتفعّل حسابك:\n\n"
    "{link}\n\n"
    "الرابط بيشتغل مرة وحدة وبينتهي بعد فترة. إذا ما طلبت هالشي، تجاهل الإيميل.\n\n"
    "مودير"
)

# Set-password flow responses.
ACTIVATION_INVALID = "رابط التفعيل مش صحيح أو خلص. اطلب رابط جديد."
ACTIVATION_DONE = "تفعّل حسابك! فيك تسجّل دخول هلّق بكلمة السر الجديدة."
