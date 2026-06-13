"""The Whisper fine-tune pipeline — the single source of truth (AD-12.1, Task 12.3).

`WhisperForConditionalGeneration` (openai/whisper-small) fine-tuned with the HF
`Seq2SeqTrainer` (`predict_with_generate=True`), WER/CER computed via evaluate/jiwer.
Both the zero-shot baseline AND the fine-tuned model are logged to results.csv, the
model + processor are saved to artifacts/, and a model_card.json is written.

Run it with one command — `python -m app.asr.train` — locally on a tiny subset for
shape, or on a Colab GPU for the full run (the notebook only imports and calls this).

SCAFFOLD (Task 12.1): the entrypoint is wired so `python -m app.asr.train` resolves;
torch/transformers/evaluate are imported LAZILY inside `train()` (AD-12.6), so this
module imports on the default path with none of the heavy stack present. The training
body lands in Task 12.3.
"""

from app.infra.logging import get_logger

log = get_logger(__name__)


def train(*, base_model: str = "openai/whisper-small", smoke: bool = False) -> None:
    """Fine-tune whisper-small on Common Voice `ar` and write the artifact + card.

    Lazy imports of torch/transformers/evaluate happen HERE (AD-12.6). `smoke=True`
    runs a tiny subset to verify the pipeline shape without a GPU. Filled in Task 12.3.
    """
    raise NotImplementedError("The fine-tune pipeline is implemented in Task 12.3.")


def main() -> None:
    log.info("asr.train.invoked")
    train()


if __name__ == "__main__":
    main()
