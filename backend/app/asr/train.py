"""The Whisper fine-tune pipeline — the single source of truth (AD-12.1, Task 12.3).

`WhisperForConditionalGeneration` (openai/whisper-small) fine-tuned with the HF
`Seq2SeqTrainer` (`predict_with_generate=True`), WER/CER via evaluate/jiwer. The pipeline:

  1. evaluate the ZERO-SHOT baseline on the val split → results.csv row,
  2. fine-tune,
  3. evaluate the fine-tuned model on the SAME split → results.csv row,
  4. save the artifact (git-ignored) + write the committed model_card.json.

Run it with ONE command:
    python -m app.asr.train            # full run (Colab GPU)
    python -m app.asr.train --smoke    # 2-step CPU shape check (no GPU, tiny subset)

The Colab notebook only imports and calls this (no training logic in the notebook).

torch / transformers / evaluate are imported LAZILY inside `train()` (AD-12.6), and this
module imports nothing from `app.infra.settings` (Colab has no Vault/DB env), so
`import app.asr.train` works on the default path with none of the heavy stack present.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.infra.logging import get_logger

log = get_logger(__name__)

DEFAULT_BASE_MODEL = "openai/whisper-small"
# The produced artifact (git-ignored — AD-12.4) and the committed card. Paths are
# relative to backend/ (the CWD on Colab after `%cd MOUDIR/backend`).
DEFAULT_OUTPUT_DIR = Path("app/asr/artifacts/whisper-small-ar")
COMMITTED_CARD_PATH = Path("app/asr/model_card.json")


def train(
    *,
    base_model: str = DEFAULT_BASE_MODEL,
    smoke: bool = False,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    num_train_epochs: float = 3.0,
    token: str | None = None,
) -> dict:
    """Fine-tune whisper-small on Common Voice `ar`; write the artifact + card + results.

    `smoke=True` runs 2 steps on a tiny subset (CPU-friendly) to verify the pipeline
    shape without a GPU. Returns the fine-tuned eval metrics dict.
    """
    # Lazy heavy imports — keep them off the default/CI path (AD-12.6).
    import evaluate
    import torch
    from transformers import (
        Seq2SeqTrainer,
        Seq2SeqTrainingArguments,
        WhisperForConditionalGeneration,
    )

    from app.asr.dataset import (
        DATA_SOURCE,
        DATASET_ID,
        load_common_voice_ar,
        normalize_arabic,
    )
    from app.asr.features import build_data_collator, build_processor, make_prepare_example
    from app.asr.model_card import ASRModelCard
    from app.asr.results import ASRExperimentResult, log_result

    output_dir = Path(output_dir)
    log.info("asr.train.start", base_model=base_model, smoke=smoke)

    # 1. Data → features.
    processor = build_processor(base_model)
    dataset, sizes = load_common_voice_ar(smoke=smoke, token=token)
    prepare = make_prepare_example(processor)
    dataset = dataset.map(prepare, remove_columns=dataset["train"].column_names, num_proc=1)

    # 2. Model — pin Arabic transcription; disable cache so gradient checkpointing works.
    model = WhisperForConditionalGeneration.from_pretrained(base_model)
    model.generation_config.language = "arabic"
    model.generation_config.task = "transcribe"
    model.generation_config.forced_decoder_ids = None
    model.config.use_cache = False

    collator = build_data_collator(processor, model.config.decoder_start_token_id)
    wer_metric = evaluate.load("wer")
    cer_metric = evaluate.load("cer")

    def compute_metrics(pred: object) -> dict[str, float]:
        label_ids = pred.label_ids
        label_ids[label_ids == -100] = processor.tokenizer.pad_token_id
        pred_str = processor.tokenizer.batch_decode(pred.predictions, skip_special_tokens=True)
        label_str = processor.tokenizer.batch_decode(label_ids, skip_special_tokens=True)
        # Score on the same normalized surface form the references were cleaned to.
        pred_str = [normalize_arabic(s) for s in pred_str]
        label_str = [normalize_arabic(s) for s in label_str]
        return {
            "wer": 100 * wer_metric.compute(predictions=pred_str, references=label_str),
            "cer": 100 * cer_metric.compute(predictions=pred_str, references=label_str),
        }

    args = Seq2SeqTrainingArguments(
        output_dir=str(output_dir),
        per_device_train_batch_size=2 if smoke else 8,
        gradient_accumulation_steps=1,
        learning_rate=1e-5,
        warmup_steps=0 if smoke else 200,
        max_steps=2 if smoke else -1,
        num_train_epochs=1 if smoke else num_train_epochs,
        gradient_checkpointing=True,
        fp16=torch.cuda.is_available(),
        eval_strategy="no",  # we evaluate manually (baseline + final) below
        per_device_eval_batch_size=2 if smoke else 8,
        predict_with_generate=True,
        generation_max_length=225,
        logging_steps=1 if smoke else 25,
        save_strategy="no",
        report_to=[],
        remove_unused_columns=False,
    )

    trainer = Seq2SeqTrainer(
        model=model,
        args=args,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        data_collator=collator,
        compute_metrics=compute_metrics,
        processing_class=processor.feature_extractor,
    )

    # 3. Zero-shot baseline on the val split (BEFORE training) — the comparison row.
    baseline = trainer.evaluate(metric_key_prefix="zeroshot")
    log_result(
        ASRExperimentResult(
            model="whisper-small-zeroshot",
            base_model=base_model,
            dataset="common_voice_ar",
            split="validation",
            hours=round(sizes.train_hours, 2),
            n_examples=sizes.n_val,
            wer=round(baseline["zeroshot_wer"], 2),
            cer=round(baseline["zeroshot_cer"], 2),
            data_source=DATA_SOURCE,
            notes="zero-shot baseline (pre-finetune)",
        )
    )

    # 4. Fine-tune, then evaluate on the SAME split.
    trainer.train()
    final = trainer.evaluate(metric_key_prefix="eval")
    log_result(
        ASRExperimentResult(
            model="whisper-small-ft",
            base_model=base_model,
            dataset="common_voice_ar",
            split="validation",
            hours=round(sizes.train_hours, 2),
            n_examples=sizes.n_val,
            wer=round(final["eval_wer"], 2),
            cer=round(final["eval_cer"], 2),
            data_source=DATA_SOURCE,
            notes="smoke" if smoke else f"epochs={num_train_epochs}",
        )
    )

    # 5. Save the artifact (git-ignored) + the committed + local model cards.
    output_dir.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(output_dir))
    processor.save_pretrained(str(output_dir))
    card = ASRModelCard(
        model=output_dir.name,
        base_model=base_model,
        language="ar",
        dataset=DATASET_ID,
        wer=round(final["eval_wer"], 2),
        cer=round(final["eval_cer"], 2),
        train_hours=round(sizes.train_hours, 2),
        n_train=sizes.n_train,
        n_eval=sizes.n_val,
        epochs=0.0 if smoke else float(num_train_epochs),
        zeroshot_wer=round(baseline["zeroshot_wer"], 2),
        split="validation",
        data_source=DATA_SOURCE,
    )
    card.save(COMMITTED_CARD_PATH)  # committed copy in app/asr/
    card.save(output_dir / "model_card.json")  # local copy beside the weights
    log.info(
        "asr.train.done",
        wer=card.wer,
        zeroshot_wer=card.zeroshot_wer,
        out=str(output_dir),
    )
    return final


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fine-tune whisper-small on Common Voice ar (Phase 12)."
    )
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument(
        "--smoke", action="store_true", help="tiny CPU shape check (2 steps, no GPU)"
    )
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parsed = parser.parse_args()
    train(
        base_model=parsed.base_model,
        smoke=parsed.smoke,
        num_train_epochs=parsed.epochs,
        output_dir=Path(parsed.output_dir),
    )


if __name__ == "__main__":
    main()
