"""User-facing signup / phone-verification messages in Lebanese Arabic.

Per the constitution, user-facing text lives in prompts/ — never inline.
Code/comments stay English; these strings reach the applicant.
"""

# The WhatsApp message that delivers the one-time code. {code} is the 6-digit
# code; the line break keeps the code easy to copy on a phone.
OTP_MESSAGE = "رمز تأكيد مدير تبعك هو: {code}\nصالح لـ 5 دقايق. لا تعطيه لحدا."

# The phone the applicant typed is not a valid Lebanese mobile number.
INVALID_PHONE = "رقم الموبايل مش صحيح. تأكّد منه (مثال: 03 123456) وجرّب مرة تانية."

# Wrong or expired code on the final signup submission.
INVALID_OTP = "رمز التأكيد غلط أو خلص وقتو. اطلب رمز جديد وجرّب مرة تانية."

# Too many code requests for the same number in a short time (abuse guard).
TOO_MANY_OTP_REQUESTS = "طلبت رموز كتير. استنى شوي وجرّب بعدين."

# We could not deliver/verify the code because a backend service is down.
OTP_SERVICE_UNAVAILABLE = "ما قدرنا نبعت الرمز هلق. حاول بعد شوي."
