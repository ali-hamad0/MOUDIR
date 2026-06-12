"""Prompt text for the Gemini OCR engine (Phase 5 seam, Gemini implementation).

Per the constitution, prompts live in prompts/ — never inline in engine code.
This is the pixels → text step ONLY: the model transcribes what is printed;
structuring (lines, quantities, totals) is the BillExtractionAgent's job.
"""

# The image is attached inline after this instruction. The model must return the
# raw text only — every printed line, exactly as written (Arabic and numbers),
# one line per printed line, no commentary, no translation, no markdown.
OCR_SYSTEM = """\
اقرأ كل النص المطبوع بهالصورة (فاتورة أو وصل من مورّد لبناني) ورجّعو متل ما هو.

القواعد:
- رجّع النص فقط، سطر بسطر متل ما هو مكتوب بالصورة.
- خلّي الأرقام والأسعار والكميات متل ما هي بالظبط، ولا تحوّل العملة.
- لا تترجم، لا تشرح، لا تضيف عناوين أو تنسيق — بس النص الخام.
- إذا في كلمة مش واضحة، اكتب أقرب قراءة لها.
"""
