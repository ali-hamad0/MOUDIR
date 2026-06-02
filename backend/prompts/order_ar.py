"""Customer-facing order-flow messages in Lebanese Arabic.

Per the constitution, all user-facing text lives in prompts/ — never inline.
Code/comments stay English; these strings reach the customer (or owner).
Placeholders ({...}) are filled by the dispatcher/agent at send time.
"""

# A registered owner messaged the shop number. Owner chat is Phase 7; until then
# we route to this placeholder (proves Phase 1 role detection works in production).
OWNER_PLACEHOLDER = (
    "أهلين! خدمة المحادثة للمالك رح تجي بمرحلة لاحقة. إذا بدك تشوف طلباتك، فيك تتطلّع من اللوحة."
)

# The customer asked for something not in the catalog. {items} lists what IS sold.
PRODUCT_NOT_IN_CATALOG = "عذراً، ما عنّا هالشي. هاي المنتجات يلي عنّا: {items}"

# The product exists but is currently turned off (is_available=False).
PRODUCT_NOT_AVAILABLE = "عذراً، هالمنتج مش متوفّر هلّق."

# Order saved. {total_lbp} is the total in LBP; {fulfillment} is the pickup/
# delivery line filled from FULFILLMENT_PICKUP / FULFILLMENT_DELIVERY below.
ORDER_CONFIRMED = "تمام! طلبك انحفظ. المجموع {total_lbp} ل.ل. {fulfillment}"

FULFILLMENT_PICKUP = "بتيجي تاخدو من المحل."
FULFILLMENT_DELIVERY = "رح يوصلك عالعنوان."

# We could not make sense of the message as an order.
DID_NOT_UNDERSTAND = "ما فهمت طلبك منيح. فيك تكتبلي شو بدّك بالظبط؟"

# An input guardrail tripped (injection / jailbreak / off-topic). Stays polite,
# never exposes internals (Task 2.10 wires this in).
RAIL_REFUSAL = "أنا مساعد المحل للطلبات بس. كيف فيني ساعدك بطلبك؟"
