"""Serve-time ASR backends (Phase 12).

This subpackage is where the heavy torch/transformers stack is allowed — and ONLY
behind a lazy import in the `whisper` branch of `build_audio_transcriber`. Importing
`app.infra.asr` itself must stay cheap; the weight lives in `whisper_transcriber`,
imported lazily so the default CI/dev path never pulls torch (AD-12.6).
"""
