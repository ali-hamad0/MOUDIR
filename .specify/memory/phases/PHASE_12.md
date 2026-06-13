# Phase 12 — Lebanese Arabic ASR (Whisper Fine-Tune)

> A fine-tuned Whisper model that transcribes Lebanese-Arabic voice notes,
> served behind the EXISTING `AudioTranscriber` seam. Speech→text is a trained
> ASR model, not an LLM prompt — the right tool for the job (Constitution IV).
> All fine-tuning and GPU compute runs on **Colab**; the repo holds the code.

Read `.specify/memory/constitution.md` FIRST (Principle IV — right tool per job;
load-once-via-lifespan; the Wall). Then `.specify/memory/ROADMAP.md`. Then this
file. Work task by task; **one task = one branch = one PR; pause for approval
after each task before committing.** Phase 11 was Billing (plans + Whish Pay);
this is the next as-built phase.

---

## Goal

Replace LLM-based voice transcription (the Gemini native-audio path from Phase
10) with a **dedicated fine-tuned Whisper ASR model** for Lebanese Arabic,
reached through the SAME `AudioTranscriber.transcribe(...)` signature it has
today — no caller in the webhook handler changes.

By the end of Phase 12:
- `transcribe_mode: "dev" | "gemini" | "whisper"` selects the backend behind
  `build_audio_transcriber` — mirroring `ocr_mode` / `ml_mode` / `whatsapp_mode`.
  CI default `"dev"` (offline stub); `"gemini"` stays the zero-artifact live
  fallback; `"whisper"` serves the fine-tuned model.
- The fine-tune pipeline lives in version-controlled `app/asr/*.py` and is the
  single source of truth; a thin Colab notebook imports it and runs the GPU
  training (AD-12.1).
- Word Error Rate (WER) is reported on a held-out split, logged to
  `app/asr/results.csv`, and floored by a golden eval in CI
  (`eval_thresholds.yaml: asr.wer_max`).
- The model is fine-tuned and validated on **public Arabic ASR** (Mozilla
  Common Voice `ar`), with the path to real Lebanese-dialect data documented
  (AD-12.5).

---

## Why this phase exists here

Phase 10 shipped voice messages by sending the OGG bytes to Gemini's
multimodal endpoint (`app/infra/audio_transcriber.py`). That works, but it is
an LLM doing a speech task, it costs a Gemini call per voice note, and it can
not be improved for Lebanese dialect. A fine-tuned Whisper is the right tool:
cheaper at volume, improvable on real owner/customer audio, and aligned with
Constitution IV ("ML/specialized models do the work; the LLM explains"). The
seam from Phase 10 was built for exactly this swap — the transcriber is already
a provider-agnostic boundary built once in lifespan.

---

## Resist scope creep

This phase is ASR, not agents, not UI, not new transport.
- **No change to the webhook handler or identity/routing.** The voice path
  (`download_media` → `transcribe` → dispatch) is untouched except that
  `transcribe` now resolves to a different backend by mode.
- **No real-time / streaming ASR.** Voice notes are short, complete OGG files;
  batch transcription of the downloaded bytes is the only mode.
- **No speaker diarization, no translation.** Lebanese Arabic audio → Lebanese
  Arabic text. Nothing more.
- **No torch in the default/CI path.** The heavy stack is quarantined to the
  whisper backend and lazily imported (AD-12.6). CI runs `"dev"`.
- **No new agent, no LLM in the transcription path.** The LLM may later read the
  transcript (existing agents); ASR itself is model-only.

---

## Prerequisites (read before Task 12.1)

- **Phase 10 is merged** — the `AudioTranscriber` seam, `build_audio_transcriber`
  factory, `app.state.audio_transcriber` lifespan wiring, and the voice path in
  the webhook handler all exist and are green.
- **ASR deps are NOT installed.** `app/asr/` does not exist yet. Task 12.1 adds
  `transformers`, `torch`, `datasets`, `evaluate`, `jiwer`, `soundfile`,
  `accelerate` via `uv`, pinned — and quarantines the torch import (AD-12.6).
- **⚠️ The data problem — flagged up front (AD-12.5 / Task 12.2).** There is **no
  clean public Lebanese-dialect ASR dataset.** Common Voice `ar` is broad/MSA-
  leaning Arabic. We therefore prove the **pipeline** on public Arabic and report
  an honest WER number on it; we do NOT claim Lebanese-dialect accuracy from MSA
  data. The path to real Lebanese audio (owner/customer voice notes, collected
  with consent, transcribed/reviewed, fed back as a fine-tune set) is documented
  and is the natural Phase-12.5 follow-up. Mirrors the Phase 6 synthetic-data
  honesty rule ([[phase6-ml-colab-decision]]).
- **⚠️ Compute.** whisper-small fine-tuning is not feasible on CPU. Training runs
  on **Colab GPU** (T4/A100). The repo holds the code + a thin notebook; the
  produced artifact is re-inserted (AD-12.1, AD-12.4) — exactly the Phase-6 Colab
  discipline.

---

## Core constraint reminder — graded every task

1. **Right tool for the job (Constitution IV).** Transcription is a trained ASR
   model (Whisper), not an LLM prompt. The fine-tune is `.py` under `app/asr/`,
   never only a notebook.
2. **Eval is the grade.** WER on a held-out split, ≥1 baseline (zero-shot
   whisper-small) vs the fine-tuned model in `results.csv`. The golden eval
   floors WER in CI; break it on purpose → CI red.
3. **Load once via lifespan, serve via DI.** The Whisper model loads ONCE in
   `lifespan` into `app.state.audio_transcriber` — NEVER inside the route. The
   seam already lives there (`build_audio_transcriber`).
4. **Secrets in Vault; httpx not requests; prompts in `prompts/`.** The Gemini
   fallback path keeps `gemini_api_key` via `get_secret_value()` and the prompt
   in `prompts/audio_ar.py`. Whisper needs no key and no prompt.
5. **Observability + privacy.** Audio bytes are never logged (only size/duration
   and resulting transcript length). The model `model_card.json` records the
   training window, dataset, and WER.
6. **The Wall.** Transcription is stateless and tenant-agnostic at the model
   level (the model is shared weights), but every transcript produced is handed
   straight back into the tenant-scoped dispatch path — no transcript is stored
   outside the tenant's conversation flow.

---

## Architecture decisions (recorded — read before building)

These are decisions for THIS phase; the constitution wins on conflict. They also
belong in `docs/DECISIONS.md` (docs/ is gitignored, stays local).

- **AD-12.1 — Code-first training, Colab for GPU.** The pipeline lives in
  `app/asr/*.py` and is the single source of truth (`python -m app.asr.train`).
  Colab is GPU compute only: a thin notebook (`notebooks/asr_finetune.ipynb`)
  pip-installs the pinned deps, imports `app.asr.*`, runs the training on a T4/
  A100, and uploads the resulting model. No training logic lives in the notebook.
  ([[phase6-ml-colab-decision]] generalized to ASR.)

- **AD-12.2 — Model choice: `whisper-small`.** ~244M params: strong multilingual
  Arabic, fine-tunes on a single Colab GPU, and runs acceptably on CPU for
  single short clips at serve time. Compare WER against zero-shot whisper-small
  (and optionally whisper-base for a lighter fallback) in `results.csv`. Record
  the choice. Serving may later use `faster-whisper` (CTranslate2) for speed —
  optional, behind the same seam.

- **AD-12.3 — One transcription seam, mode-selected.** Extend the existing
  `build_audio_transcriber(settings)` factory to return, by `transcribe_mode`:
  `DevAudioTranscriber` (canned Arabic stub — today's dev behavior),
  `GeminiAudioTranscriber` (today's live REST path, refactored out of the
  `whatsapp_mode` branch), or `WhisperTranscriber` (the fine-tuned model). All
  three implement the SAME `async transcribe(audio_bytes, mime_type) -> str`.
  This mirrors `OCREngine` (`ocr_mode`) and the Phase 6 predictors (`ml_mode`).

- **AD-12.4 — The model artifact is too big to commit.** A fine-tuned
  whisper-small is ~1 GB. Unlike the Phase-6 `.joblib` files (AD-6.4, committed),
  the Whisper artifact is **git-ignored** and stored out-of-repo (a GitHub
  Release asset, Hugging Face Hub repo, or the project MinIO bucket). Settings
  carries `whisper_model_uri` / local path; a documented fetch step pulls it
  before `transcribe_mode="whisper"` is used. CI/dev never need it (default
  `"dev"`/`"gemini"`). The `model_card.json` (small) IS committed.

- **AD-12.5 — Data: validate on public Arabic, document the Lebanese path.**
  Fine-tune/eval on Mozilla **Common Voice `ar`** (16 kHz mono). Honesty rule:
  it is broad/MSA-leaning, not Lebanese dialect — we claim the pipeline is
  correct and report a real WER on public Arabic; we do NOT claim dialect
  accuracy. `DECISIONS.md` records the caveat and the path to real Lebanese audio
  (consented owner/customer voice notes → transcribe/review → dialect fine-tune
  set). Mirrors the Phase-6 synthetic-data honesty rule.

- **AD-12.6 — torch quarantined and lazily imported.** `torch` + `transformers`
  add ~2 GB to the image and must NOT load on the CI/dev path. The whisper
  backend lives in `app/infra/asr/whisper_transcriber.py`; `build_audio_transcriber`
  imports it **lazily inside the `whisper` branch only** — exactly how
  `build_ocr_engine` lazily imports `cloud_vision` / `gemini_vision`. A CI guard
  asserts torch is not imported under the default mode. Gemini remains the
  zero-heavy-dep live fallback.

- **AD-12.7 — Eval is the grade (WER).** `app/asr/results.csv` logs every run
  (model, dataset, hours, WER, CER, timestamp). A small golden set of held-out
  Arabic clips + reference transcripts floors WER in `eval_thresholds.yaml`
  (`asr.wer_max`). The CI eval job runs against the real artifact when present,
  and is skipped/marked clearly when absent (default).

---

## New / changed config & model (read before you build)

Phase 12 adds **no DB schema.** The only additions are config + a new module:

- **Settings** (mirroring `ocr_mode`): `transcribe_mode: "dev" | "gemini" |
  "whisper"` (default `"dev"`), `whisper_model_uri` (where to fetch the artifact),
  `whisper_model_path` (local cache path), `whisper_device` (`"cpu"` default).
- **`app/asr/`** — the training package (see Task 12.1 layout).
- **`app/infra/asr/whisper_transcriber.py`** — the serve-time backend (lazy torch).
- The existing `app/infra/audio_transcriber.py` is refactored so the dev/gemini
  logic is selected by `transcribe_mode` instead of `whatsapp_mode` (behavior
  preserved: old dev→`dev`, old live→`gemini`).

---

## The Phase 12 shape (what we wire end-to-end)

```
            ┌─────────────────── training (Colab GPU, one command) ──────────────────┐
 Common     │  datasets(ar) ──► prepare: 16kHz mono + WhisperProcessor features       │
 Voice ar ─►│        │                    │                                           │
            │        ▼                    ▼                                           │
            │  Seq2SeqTrainer (whisper-small)  ──►  WER/CER on held-out split          │
            │        │                    │                                           │
            │        ▼                    ▼                                           │
            │  results.csv (zero-shot vs fine-tuned)   model artifact + model_card.json│
            └───────────────────────────────────────────────────────────────────────┘
                                          │ artifact (git-ignored: Release/HF/MinIO, AD-12.4)
                                          ▼  fetch
 startup (lifespan) ──► build_audio_transcriber(settings) by transcribe_mode:
                          dev → stub │ gemini → REST (Phase 10) │ whisper → load model ONCE
                                          │ DI: app.state.audio_transcriber
                                          ▼
        webhook voice path (UNCHANGED): download_media(media_id) ──► transcribe(bytes) ──► dispatch
```

CI default is `transcribe_mode="dev"` (offline stub, no torch, no network) —
exactly like `ocr_mode=stub` / `ml_mode=stub`. The golden eval (Task 12.6) loads
the real artifact when present and asserts WER doesn't regress.

---

## Phase 12 — Tasks Overview

| # | Task | Branch |
|---|------|--------|
| 12.1 | ASR deps (pinned, torch quarantined) + `app/asr/` layout + `results.csv` + `transcribe_mode` Settings seam | `feature/MOD-12-asr-scaffold` |
| 12.2 | Dataset prep: Common Voice `ar` loader, 16 kHz mono resample, train/val/test splits, Colab-friendly exporter | `feature/MOD-12-asr-data` |
| 12.3 | **Fine-tune pipeline**: whisper-small via HF `Seq2SeqTrainer`, WER/CER metric, `results.csv`, thin Colab notebook (GPU) | `feature/MOD-12-asr-train` |
| 12.4 | `WhisperTranscriber` backend (lazy torch) + refactor `build_audio_transcriber` to `transcribe_mode` + lifespan load-once | `feature/MOD-12-asr-serve` |
| 12.5 | Wire the voice path to `transcribe_mode` (dev/gemini/whisper); Gemini stays the zero-artifact fallback | `feature/MOD-12-asr-wire` |
| 12.6 | WER golden eval set + `eval_thresholds.yaml` (`asr.wer_max`) + artifact-aware CI eval job | `feature/MOD-12-asr-eval` |
| 12.7 | CI guards (ASR offline by default, torch not imported in default path) + `DECISIONS.md` + artifact-fetch doc | `feature/MOD-12-asr-ci` |

7 tasks. One branch / one PR / approval each.

---

## Task 12.1 — ASR deps + `app/asr/` layout + results.csv + Settings seam

Add via `uv` (pinned): `transformers`, `torch`, `datasets`, `evaluate`, `jiwer`,
`soundfile`, `accelerate`. Quarantine torch/transformers usage to the train
package and the (Task 12.4) whisper backend. Lay out:

```
app/asr/
  __init__.py
  dataset.py      # Common Voice ar load + 16kHz resample + splits (Task 12.2)
  features.py     # WhisperProcessor feature extraction + data collator
  train.py        # Seq2SeqTrainer fine-tune, WER metric, results.csv (Task 12.3)
  eval.py         # WER/CER on a split or the golden set (Task 12.6)
  model_card.py   # write model_card.json (dataset, hours, window, WER)
  results.csv     # experiment log (header committed; rows appended by training)
  artifacts/      # local cache for the fetched/produced model (git-ignored, AD-12.4)
notebooks/
  asr_finetune.ipynb  # thin Colab GPU runner that imports app.asr.* (AD-12.1)
```

Add Settings seams: `transcribe_mode` (default `"dev"`), `whisper_model_uri`,
`whisper_model_path`, `whisper_device` (`"cpu"`). **DoD:** `uv sync` resolves
pinned; `import app.asr` works WITHOUT importing torch at module top
(lazy in train/serve); CI import + forbidden-pattern + provider-SDK gates green;
`results.csv` exists with a header; `.gitignore` excludes `app/asr/artifacts/`.

## Task 12.2 — Dataset prep (Common Voice `ar`)

`app/asr/dataset.py`: load Common Voice `ar` via `datasets`, cast audio to
16 kHz mono, normalize/clean reference text (strip diacritics policy decided +
recorded), and produce deterministic train/val/test splits. A small exporter
writes a Colab-ready subset (so the notebook trains without re-streaming the
whole set). **Honesty:** document in the card that this is public Arabic, not
Lebanese dialect (AD-12.5). **DoD:** loader returns splits with audio arrays at
16 kHz + cleaned references; sizes/hours logged; a tiny smoke subset prepares
end-to-end offline-ish (cached); the MSA caveat is written down.

## Task 12.3 — Fine-tune pipeline (whisper-small) + Colab notebook

`app/asr/train.py`: `WhisperProcessor` + `WhisperForConditionalGeneration`
(`openai/whisper-small`), a sequence-to-sequence data collator, `Seq2SeqTrainer`
with `predict_with_generate`, WER (+CER) via `evaluate`/`jiwer`. Log zero-shot
baseline AND fine-tuned WER to `results.csv`. Save the model + processor to
`artifacts/` and write `model_card.json` (dataset, hours, epochs, WER/CER,
window). `notebooks/asr_finetune.ipynb` is the GPU runner (AD-12.1): installs
pinned deps, imports `app.asr.train`, runs it on Colab, uploads the artifact.
**DoD:** running the pipeline (small subset locally for shape, full run on Colab)
produces an artifact + card; `results.csv` has ≥2 rows (zero-shot vs fine-tuned);
WER improves over zero-shot on the val split; one-command retrain documented.

## Task 12.4 — `WhisperTranscriber` backend + `transcribe_mode` refactor

`app/infra/asr/whisper_transcriber.py`: `WhisperTranscriber.transcribe(
audio_bytes, mime_type) -> str` — decode bytes (soundfile) to 16 kHz mono,
run the loaded model, return text. **torch/transformers imported lazily inside
this module only** (AD-12.6); the model loads ONCE (passed in from lifespan, not
per-call). Refactor `build_audio_transcriber(settings)` to switch on
`transcribe_mode`: `dev` → stub, `gemini` → the existing REST path (moved out of
the `whatsapp_mode` branch, behavior preserved), `whisper` → `WhisperTranscriber`
with a lazily-imported model loaded once. **DoD:** factory returns the right
backend per mode; `app.state.audio_transcriber` still set once in lifespan; a
unit test covers dev + gemini selection WITHOUT importing torch; whisper path
tested behind a skip/mark when no artifact present.

## Task 12.5 — Wire the voice path to `transcribe_mode`

The webhook voice branch (`download_media` → `transcribe`) is unchanged — it
already calls `app.state.audio_transcriber.transcribe(...)`. This task only
ensures mode selection is correct end-to-end and that `gemini` remains the
default LIVE backend until a Whisper artifact is fetched (so live WhatsApp keeps
working with zero new artifact). **DoD:** with `transcribe_mode="gemini"` the
Phase-10 behavior is byte-for-byte preserved; with `"dev"` the stub returns;
with `"whisper"` (artifact present) a real OGG transcribes; audio bytes never
logged.

## Task 12.6 — WER golden eval + threshold + CI job

A small committed golden set: held-out Arabic clips (or references to cached
Common Voice ids) + reference transcripts. `app/asr/eval.py` computes WER/CER
against the artifact. `eval_thresholds.yaml` gains `asr: { wer_max: <X> }`
(justified from the Task 12.3 numbers). A CI step runs the eval when the artifact
is present and is clearly skipped otherwise. **DoD:** with the artifact, the eval
runs and passes under `wer_max`; intentionally degrading the model fails the job;
the threshold is documented.

## Task 12.7 — CI guards + DECISIONS + fetch doc

CI guards (no DB, no network, no torch): `transcribe_mode` defaults to `"dev"`;
the default path does not import torch; `WhisperTranscriber` and `app.asr` are
importable (lazily) without pulling torch at module top; `eval_thresholds.yaml`
has `asr.wer_max`. `docs/DECISIONS.md` records: model choice + WER table, the
public-Arabic-vs-Lebanese honesty caveat + the dialect-data path, the artifact
storage/fetch policy (AD-12.4), and the torch-quarantine rationale. **DoD:** CI
green with ASR offline; every Phase 12 defend-it question answerable from
DECISIONS + the model card; the artifact fetch is a documented one-liner.

---

## Phase 12 — Definition of Done

- [ ] `transcribe_mode` selects dev / gemini / whisper behind the SAME
  `AudioTranscriber.transcribe(...)` signature; default `"dev"`; no caller in the
  webhook handler changes.
- [ ] The fine-tune pipeline is version-controlled `.py` under `app/asr/`,
  runnable with one command; the Colab notebook is a thin GPU runner only.
- [ ] `results.csv` shows zero-shot vs fine-tuned WER/CER; fine-tuned improves on
  the val split.
- [ ] The model loads ONCE via lifespan (never in a route); torch is imported
  lazily and only on the `whisper` path.
- [ ] A WER golden eval floors quality in CI (`asr.wer_max`); break it on purpose
  → CI red.
- [ ] Public-Arabic-vs-Lebanese honesty caveat + the dialect-data path are in
  `DECISIONS.md`; the model card records dataset, hours, window, and WER.
- [ ] The artifact is git-ignored and fetched via a documented step; CI/dev run
  without it (`dev`/`gemini`).
- [ ] CI green (suite offline via `dev`; default path imports no torch);
  host-verified with `uv run pytest` (Docker DNS blocked in-container).
- [ ] Audio bytes are never logged (size/duration + transcript length only).

## Common pitfalls

- Importing torch/transformers at module top — it loads on every CI run and
  bloats startup. Lazy-import inside the whisper branch only (AD-12.6).
- Loading the Whisper model inside the route handler instead of lifespan.
- Claiming Lebanese-dialect accuracy from Common Voice (MSA-leaning) — report
  what you actually measured and document the dialect-data path.
- Committing the ~1 GB artifact. Git-ignore it; store it out-of-repo (AD-12.4).
- Training in the notebook as the source of truth. The notebook only RUNS
  `app.asr.*` on a GPU.
- Forgetting to resample to 16 kHz mono — Whisper expects it; wrong sample rate
  silently wrecks WER.
- Breaking the Phase-10 Gemini path during the refactor — `gemini` mode must be
  byte-for-byte the old behavior.

## Defend-it questions

- Why a fine-tuned Whisper instead of the Gemini transcription you already had?
  (cost at volume, improvable on dialect, right tool — Constitution IV)
- Show me where the model loads. How many times per process? (lifespan, once)
- Your data is Common Voice Arabic — what can you honestly claim, and what can't
  you? What's the path to real Lebanese dialect?
- Where does torch get imported, and how do you keep it off the CI/dev path?
- How do you select dev vs gemini vs whisper? Show the factory.
- What's your WER, on what split, vs what baseline? (point to results.csv)
- How big is the artifact, where does it live, and how does the app fetch it?
- A voice note arrives in live mode with no Whisper artifact present — what
  happens? (falls back to `gemini`, transcribes, never crashes)

## Ready for the next phase?

You are ready when:
- Every checkbox above is checked and the defend-it questions answer fluently.
- `cd backend && uv run pytest tests` is green with `transcribe_mode="dev"`
  (no torch on that path), and the WER golden eval passes against the fetched
  artifact.
- A demo shows a real Lebanese-Arabic OGG transcribed by the fine-tuned model
  through the unchanged webhook voice path — and the same path falling back to
  `gemini` when the artifact is absent.

The natural follow-up (Phase 12.5, not in scope here) is collecting consented
real Lebanese-dialect audio and re-running this exact pipeline on it — the code
does not change, only the dataset does.
