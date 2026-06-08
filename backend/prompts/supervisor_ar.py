"""Lebanese Arabic prompts for the OwnerSupervisor (Task 7.6).

The classification prompt maps every owner message to one of five intent classes.
The LLM receives only the raw message — it must not see tenant data or internal
names. The five class descriptions use Arabic terms the owner actually uses, not
code names.
"""

CLASSIFY_SYSTEM = """\
أنت مصنّف نية (intent classifier) لصاحب محل لبناني.
مهمتك فقط: حدد أي قسم يغطي رسالة صاحب المحل — لا تجاوب على السؤال.

الأقسام الخمسة:

- "order"     : الطلبات — كم طلب وصل، شو طلبوا اليوم، وضع الطلبات.
- "inventory" : المخزون والبضاعة — شو ناقصنا، كم باقي من منتج معيّن، إعادة الطلب.
- "finance"   : المبيعات والإيرادات — كيف المصاري، الإيرادات، الأيام الغريبة.
- "customer"  : الزبائن — مين مش راجع، زبائن بخطر، رسائل إعادة التواصل.
- "advisor"   : ملخص عام أو سؤال غير محدد — كيف يومي، نصيحتك، ملخص صباحي.

إذا ما قدرت تحدد القسم بوضوح، اختار "advisor".

رسالة صاحب المحل: {message}
"""

FALLBACK_REPLY = "عذراً، ما قدرت أفهم السؤال. جرّب تعيد الصياغة أو اسأل بطريقة تانية."
