"""Whisper feature extraction + the seq2seq data collator (Task 12.3).

Turns 16 kHz audio arrays into log-mel input features via `WhisperProcessor` and pads
batches: input features and the tokenized label ids are padded SEPARATELY, and the label
pad is replaced by -100 so the loss ignores it. This is the bridge between the cleaned
dataset (dataset.py) and the trainer (train.py).

`transformers` is imported LAZILY inside `build_processor` (AD-12.6). The collator
references only objects the processor hands back (it calls torch *tensor methods* on
them but never imports torch here), so `import app.asr.features` stays torch-free on the
default CI/dev path with none of the heavy stack installed.
"""

from __future__ import annotations

from dataclasses import dataclass

# Whisper is multilingual; pin the decoder to Arabic transcription (not translation) so
# generation produces Arabic text. Full language name is what WhisperProcessor expects.
WHISPER_LANGUAGE = "Arabic"
WHISPER_TASK = "transcribe"


def build_processor(base_model: str = "openai/whisper-small") -> object:
    """Construct the WhisperProcessor (feature extractor + tokenizer) for Arabic.

    Lazy import of `transformers` inside (AD-12.6).
    """
    from transformers import WhisperProcessor

    return WhisperProcessor.from_pretrained(
        base_model, language=WHISPER_LANGUAGE, task=WHISPER_TASK
    )


def make_prepare_example(processor: object):
    """Return a `ds.map` function that adds `input_features` + `labels` to a row.

    The reference text is the ALREADY-normalized `sentence` (dataset.py applied the
    recorded policy), so the labels are tokenized from the same surface form WER scores.
    """

    def prepare(batch: dict) -> dict:
        audio = batch["audio"]
        batch["input_features"] = processor.feature_extractor(
            audio["array"], sampling_rate=audio["sampling_rate"]
        ).input_features[0]
        batch["labels"] = processor.tokenizer(batch["sentence"]).input_ids
        return batch

    return prepare


@dataclass
class DataCollatorSpeechSeq2SeqWithPadding:
    """Pad a batch of audio features + label ids independently (the standard Whisper
    fine-tune collator). torch is never imported here — `processor.pad(return_tensors=
    "pt")` returns tensors and we only call tensor methods on them (AD-12.6)."""

    processor: object
    decoder_start_token_id: int

    def __call__(self, features: list[dict]) -> dict:
        # Audio inputs are already fixed-length log-mel; pad them as tensors.
        input_features = [{"input_features": f["input_features"]} for f in features]
        batch = self.processor.feature_extractor.pad(input_features, return_tensors="pt")

        # Label ids are variable length; pad, then mask pad positions to -100 (ignored
        # by the loss).
        label_features = [{"input_ids": f["labels"]} for f in features]
        labels_batch = self.processor.tokenizer.pad(label_features, return_tensors="pt")
        labels = labels_batch["input_ids"].masked_fill(labels_batch.attention_mask.ne(1), -100)

        # If every sequence starts with the decoder start token, drop it — the model
        # prepends it during generation.
        if (labels[:, 0] == self.decoder_start_token_id).all().cpu().item():
            labels = labels[:, 1:]

        batch["labels"] = labels
        return batch


def build_data_collator(
    processor: object, decoder_start_token_id: int
) -> DataCollatorSpeechSeq2SeqWithPadding:
    """Construct the seq2seq data collator bound to this processor + model start token."""
    return DataCollatorSpeechSeq2SeqWithPadding(
        processor=processor, decoder_start_token_id=decoder_start_token_id
    )
