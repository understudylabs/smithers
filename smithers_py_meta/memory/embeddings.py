"""Pluggable embedding adapters for semantic recall.

Defines the ``EmbeddingAdapter`` protocol and two built-in
implementations: ``OpenAIEmbeddingAdapter`` (requires the ``openai``
package and reads ``OPENAI_API_KEY`` from env) and
``NullEmbeddingAdapter`` (zero vectors for tests).

Includes pure-stdlib helpers for packing/unpacking vectors as
little-endian float32 BLOBs and computing cosine similarity.
"""

from __future__ import annotations

import math
import os
import struct
from typing import Protocol


class EmbeddingAdapter(Protocol):
    """Protocol for embedding providers.

    ``model`` is a stable tag persisted alongside each fact's embedding —
    facts whose stored ``embedding_model`` doesn't match the current
    adapter's ``model`` are skipped during recall to prevent mixing
    incompatible embedding spaces.
    """

    @property
    def model(self) -> str:
        """Model tag (e.g., 'text-embedding-3-small')."""
        ...

    @property
    def dimensions(self) -> int:
        """Vector dimensionality."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per input."""
        ...


class OpenAIEmbeddingAdapter:
    """OpenAI embedding adapter.

    Requires the ``openai`` package. Reads ``OPENAI_API_KEY`` from env if
    ``api_key`` is not provided.
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError(
                "OpenAIEmbeddingAdapter requires the 'openai' package. "
                "Install it with: pip install openai"
            ) from e

        self._model = model
        self._dimensions = 1536 if "small" in model else 3072
        self._client = AsyncOpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via OpenAI's embeddings API."""
        if not texts:
            return []
        response = await self._client.embeddings.create(
            input=texts,
            model=self._model,
        )
        return [item.embedding for item in response.data]


class NullEmbeddingAdapter:
    """Null embedding adapter that returns zero vectors.

    Useful for tests where semantic recall isn't needed.
    """

    def __init__(self, dimensions: int = 8):
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return "null"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return zero vectors for each input."""
        return [[0.0] * self._dimensions for _ in texts]


def pack_vector(vec: list[float]) -> bytes:
    """Pack a vector as little-endian float32 BLOB."""
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    """Unpack a little-endian float32 BLOB into a vector."""
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors (pure stdlib)."""
    if len(a) != len(b):
        raise ValueError(f"Vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
