"""Embedder contract (Protocol): the seam a real embedding model will satisfy.

Key requirement for scale: all calls are BATCHED (list in -> matrix out). The
pipeline must never call an embedder one text at a time.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str], batch_size: int | None = None) -> list[list[float]]:
        """Return one vector (list[float]) per input text, in the same order.

        Must be deterministic for identical inputs and must accept at least
        `batch_size` texts per call. Determinism is PER-PATH (ADR-010):
        bit-exact for CPU and `DummyEmbedder`; cosine-stable for GPU-fp16
        inference — L2-normalized vectors whose cosine similarity to a
        canonical re-embed is >= 0.9999 (never bit-exact: GPU reduction order
        is nondeterministic). `name` must carry model identity + revision +
        dtype (e.g. "BAAI/bge-m3@3f9a1c2b-fp16") so artifact keys are
        unambiguous across models/dtypes.
        """
        ...