"""Pluggable embedding adapters for semantic recall.

A memory store needs embeddings only when semantic recall is used; the
working-memory and message-history surfaces work without any embedding
backend. So the adapter is configured separately and the store accepts
``None`` to disable semantic recall entirely.

Two built-ins:

- ``OpenAIEmbeddingAdapter`` — calls the OpenAI ``text-embedding-3-small``
  endpoint (1536 dims, $0.02 / 1M tokens as of 2026-05). Requires
  ``OPENAI_API_KEY`` and the optional ``openai`` package.
- ``NullEmbeddingAdapter`` — returns zero vectors. Useful for tests and
  for the case where semantic recall is disabled but the API surface
  still needs a non-None adapter.

A user-defined backend just implements the ``EmbeddingAdapter`` Protocol.
"""

from __future__ import annotations

import os
import struct
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingAdapter(Protocol):
    """Interface every embedding backend implements."""

    @property
    def model(self) -> str:
        """Model identifier persisted with each embedding for cache invalidation."""

    @property
    def dimensions(self) -> int:
        """Vector dimensionality. Must be constant across calls."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of strings. Length of result == length of input."""


def pack_vector(vec: list[float]) -> bytes:
    """Pack a float vector as little-endian float32 bytes for SQLite BLOB."""
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes, dimensions: int) -> list[float]:
    """Inverse of ``pack_vector``."""
    if len(blob) != dimensions * 4:
        raise ValueError(
            f"embedding blob has {len(blob)} bytes but dimensions={dimensions} expects {dimensions * 4}"
        )
    return list(struct.unpack(f"<{dimensions}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]. Both vectors must have the same length."""
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / ((norm_a**0.5) * (norm_b**0.5))


class NullEmbeddingAdapter:
    """Zero-vector embeddings. Use for tests or to disable semantic recall.

    All embeddings come out equal so semantic recall returns facts in
    insertion order; this is intentional — it lets callers exercise the
    full API surface without spending tokens on a real embedding model.
    """

    def __init__(self, dimensions: int = 8) -> None:
        self._dim = dimensions

    @property
    def model(self) -> str:
        return f"null-{self._dim}"

    @property
    def dimensions(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dim for _ in texts]


class OpenAIEmbeddingAdapter:
    """Embedding adapter backed by the OpenAI embeddings API.

    Defaults to ``text-embedding-3-small`` (1536 dimensions, cheapest
    OpenAI option). Pass ``model="text-embedding-3-large"`` for higher
    quality at higher cost.

    Requires the ``openai`` Python package (``uv pip install openai``)
    and ``OPENAI_API_KEY`` set in the environment.
    """

    DEFAULT_MODEL = "text-embedding-3-small"
    DEFAULT_DIMENSIONS = 1536
    DIMENSIONS_BY_MODEL: dict[str, int] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "OpenAIEmbeddingAdapter requires the 'openai' package. "
                "Install with: uv pip install openai"
            ) from exc

        self._model = model
        self._client = AsyncOpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url,
        )
        self._dimensions = dimensions or self.DIMENSIONS_BY_MODEL.get(
            model, self.DEFAULT_DIMENSIONS
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # OpenAI API supports batching natively — one round trip.
        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        return [item.embedding for item in response.data]
