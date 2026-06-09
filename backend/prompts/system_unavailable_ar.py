"""Lebanese Arabic message sent when all LLM providers are unavailable.

Used by:
- OwnerSupervisor.handle() as the fallback reply on LLMUnavailable
- MessageDispatcher as the belt-and-suspenders catch if the supervisor re-raises
"""

REPLY = "الخدمة مش متاحة هلّق. حاول مرة تانية بعد شوي، أو ادخل الطلب يدوياً من اللوحة."
