"""Embedding client seam (Phase 5). Text → vector, for the RAG corpora.

Provider-agnostic, mirroring the OCR seam / EmailSender / LLMRouter: callers depend
on the `EmbeddingClient` Protocol, never on a concrete provider. Two
implementations, selected by `Settings.embedding_mode`:

- `StubEmbeddingClient` — a DETERMINISTIC hashed pseudo-embedding for CI/tests and
  the dev default. Offline, no network, no key. The same text always maps to the
  same unit vector, so retrieval is exact-match-friendly in tests (an identical
  query and chunk are maximally similar) without any model.
- `GeminiEmbeddingClient` (in `app/agents/llm/gemini_embeddings.py`) — the real
  embedder, keyed by the existing Vault `gemini_api_key` (no new secret). It lives
  under `app/agents/llm/` because it imports the Google GenAI SDK, which the
  constitution's provider-agnostic boundary (CI-enforced) confines to that one
  directory alongside the LLM router.

The stub and the real client both return unit-norm vectors of `settings.embedding_dim`
length, so they are interchangeable behind the Protocol and match the pgvector column
width (Task 5.14).
"""

import hashlib
from typing import Protocol

import numpy as np

from app.infra.logging import get_logger
from app.infra.settings import Settings

log = get_logger(__name__)


class EmbeddingClient(Protocol):
    """The only way app code turns text into vectors. Callers depend on this
    Protocol; swapping or adding a provider is a config change (`embedding_mode`)
    behind the factory, never a change in the worker or the retrieval tool."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into unit-norm vectors of `embedding_dim` length,
        in order. Raises on a hard provider failure (the worker treats that as a
        retryable embedding failure)."""
        ...

    async def embed_one(self, text: str) -> list[float]:
        """Embed a single text (convenience for a query at retrieval time)."""
        ...


def _unit(vec: np.ndarray) -> np.ndarray:
    """Normalize to unit length so cosine distance behaves; a zero vector stays
    zero (degenerate, but never NaN)."""
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        return vec
    return vec / norm


class StubEmbeddingClient:
    """Deterministic, offline embeddings for CI/tests (and dev without a key).

    A text is hashed into a fixed seed; a seeded RNG produces a stable vector that is
    then unit-normalized. The SAME text always yields the SAME vector, so a query that
    matches a stored chunk's text is maximally similar — enough to test tenant-scoped
    retrieval and re-embed staleness without any model or network.
    """

    def __init__(self, dim: int) -> None:
        self._dim = dim

    def _vec(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big")
        rng = np.random.default_rng(seed)
        return _unit(rng.standard_normal(self._dim)).astype(float).tolist()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    async def embed_one(self, text: str) -> list[float]:
        return self._vec(text)


def build_embedding_client(settings: Settings) -> EmbeddingClient:
    """Construct the embedding client for the configured `embedding_mode` (built once
    in lifespan / the worker builder).

    The Gemini implementation (and its SDK) is imported lazily HERE so the stub path —
    CI, tests, dev without a key — never needs the provider SDK and never touches the
    network.
    """
    mode = settings.embedding_mode
    if mode == "gemini":
        # Imported from app/agents/llm/ — the one directory the provider SDK is
        # confined to (CI-enforced boundary). Lazy so the stub path never needs it.
        from app.agents.llm.gemini_embeddings import GeminiEmbeddingClient

        log.info("embeddings.client.selected", mode=mode, dim=settings.embedding_dim)
        return GeminiEmbeddingClient(settings)
    if mode != "stub":
        raise ValueError(f"unknown embedding_mode: {mode!r}")
    log.info("embeddings.client.selected", mode=mode, dim=settings.embedding_dim)
    return StubEmbeddingClient(settings.embedding_dim)
