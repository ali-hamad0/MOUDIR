"""Prompt constants for voice-message transcription (Phase 10, Task 10.5).

These are passed to Gemini's multimodal endpoint alongside the raw audio bytes.
Keeping prompts here (not inline in the transcriber) lets us version and diff
them like any other text asset.
"""

TRANSCRIPTION_SYSTEM = (
    "أنت مساعد متخصص في نسخ الرسائل الصوتية باللهجة اللبنانية. "
    "انسخ الرسالة الصوتية التالية بالضبط كما هي منطوقة، باللهجة اللبنانية العامية. "
    "لا تترجم ولا تعدّل، فقط اكتب ما تسمعه. "
    "أجب بالنص المنسوخ فقط، بدون أي مقدمة أو شرح."
)

# Returned in dev mode instead of calling the real API — a clearly synthetic
# stub so nobody mistakes it for a real transcription during testing.
DEV_STUB = "(رسالة صوتية - النص التجريبي)"
