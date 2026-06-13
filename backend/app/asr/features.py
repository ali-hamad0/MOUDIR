"""Whisper feature extraction + the seq2seq data collator (Task 12.2/12.3).

Turns 16 kHz audio arrays into log-mel input features via `WhisperProcessor` and pads
batches (input features and the tokenized label ids are padded separately, with the
label pad replaced by -100 so it is ignored by the loss). This is the bridge between
the cleaned dataset (dataset.py) and the trainer (train.py).

SCAFFOLD (Task 12.1): contract fixed here; `transformers` is imported LAZILY inside
the builders (AD-12.6), so `import app.asr.features` works with none of it installed.
"""


def build_processor(base_model: str = "openai/whisper-small") -> object:
    """Construct the WhisperProcessor (feature extractor + tokenizer) for Arabic.

    Lazy import of `transformers` inside (AD-12.6). Filled in Task 12.2.
    """
    raise NotImplementedError("Feature extraction is implemented in Task 12.2.")


def build_data_collator(processor: object) -> object:
    """Construct the seq2seq data collator that pads audio features and label ids.

    Filled in Task 12.3.
    """
    raise NotImplementedError("The data collator is implemented in Task 12.3.")
