"""A deterministic, dependency-free placeholder embedder.

Purpose: keep the whole pipeline runnable + testable end-to-end TODAY, shaped
exactly like the future GPU embedder (batched, deterministic). Real model (
e.g. BGE/e5) replaces this via the `Embedder` protocol with zero call-site
changes. Read the constructor docstring: this is NOT a real embedding.
"""
from __future__ import annotations

import hashlib


class DummyEmbedder:
    """Feature-hash character unigrams+bigrams into a fixed-dim vector.

    Deterministic and idempotent ONLY for a fixed `dim` and `seed`. Useful for
    pipeline wiring, volume/order correctness, and tests — NOT for semantic
    fidelity.
    """

    name = "dummy-feature-hash"

    def __init__(self, dim: int = 64, seed: int = 0):
        self.dim = dim
        self.seed = seed

    def _tokens(self, text: str) -> list[str]:
        text = (text or "").lower()
        toks = list(text)
        toks += [text[i:i + 2] for i in range(len(text) - 1)]
        return toks

    def _hash_token(self, tok: str) -> int:
        h = int(hashlib.sha256(f"{self.seed}:{tok}".encode()).hexdigest()[:8], 16)
        return h & 0xFFFFFFFF

    def embed_text(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in self._tokens(text):
            idx = self._hash_token(tok) % self.dim
            v[idx] += 1.0
        n = len(text)
        return [x / (n or 1) for x in v]

    def embed(self, texts: list[str], batch_size: int | None = None) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]