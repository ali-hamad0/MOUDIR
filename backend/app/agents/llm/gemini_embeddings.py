"""Google Gemini embedding client (Phase 5) — the real RAG embedder.

This lives under `app/agents/llm/` because it imports the Google GenAI SDK, and the
constitution's provider-agnostic boundary (enforced by CI) confines that SDK to this
ONE directory — the same place the LLM router lives. The provider-agnostic
`EmbeddingClient` Protocol + stub + factory stay in `app/infra/embeddings.py` (no SDK
there); only this concrete provider client lives next to the router.

The key is the existing Vault-resolved `Settings.gemini_api_key` — NO new secret to
seed. Vectors are unit-normalized to match the stub (so cosine distance behaves the
same), and validated to `settings.embedding_dim` so they match the pgvector column.

Verified on the HOST venv (Docker DNS is blocked in-container); CI/tests use the stub.
"""

import numpy as np

from app.infra.logging import get_logger
from app.infra.settings import Settings

log = get_logger(__name__)


class GeminiEmbeddingClient:
    """Embeddings via Google's text-embedding model (langchain-google-genai).

    Built once (lifespan / worker builder). The underlying client is created lazily
    on first use and reused.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None  # built lazily — see _get_client

    def _get_client(self):
        if self._client is not None:
            return self._client
        # Imported inside the method so merely importing this module does not require
        # the SDK; the factory already gates on mode.
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        self._client = GoogleGenerativeAIEmbeddings(
            model=self._settings.embedding_model,
            google_api_key=self._settings.gemini_api_key.get_secret_value(),
        )
        return self._client

    def _normalize(self, raw: list[float]) -> list[float]:
        vec = np.asarray(raw, dtype=float)
        dim = self._settings.embedding_dim
        if vec.shape[0] != dim:
            raise RuntimeError(
                f"embedding dim {vec.shape[0]} != configured {dim} "
                f"(model {self._settings.embedding_model})"
            )
        norm = float(np.linalg.norm(vec))
        return (vec / norm if norm else vec).tolist()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()
        raw = await client.aembed_documents(texts)
        log.info("embeddings.gemini.embed", count=len(texts))
        return [self._normalize(v) for v in raw]

    async def embed_one(self, text: str) -> list[float]:
        client = self._get_client()
        raw = await client.aembed_query(text)
        return self._normalize(raw)
