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

        Must be deterministic for identical inputs (idempotent for a given
        model version) and must accept at least `batch_size` texts per call.
        """
        ...