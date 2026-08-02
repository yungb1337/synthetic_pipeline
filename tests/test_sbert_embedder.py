"""Tests for the real local embedder (skipped when torch/the model is absent)."""
from __future__ import annotations

import pytest

from app.embedding.sbert import SentenceTransformerEmbedder


def _available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _available(), reason="sentence-transformers/torch unavailable")


@pytest.fixture(scope="module")
def embed():
    return SentenceTransformerEmbedder(model="BAAI/bge-small-en-v1.5")


def test_dims(embed):
    vecs = embed.embed(["a", "bb"], batch_size=2)
    assert len(vecs) == 2
    assert len(vecs[0]) == embed.dim


def test_deterministic(embed):
    text = "the patient has diabetes and takes metformin"
    a = embed.embed([text], batch_size=1)[0]
    b = embed.embed([text], batch_size=1)[0]
    assert a == pytest.approx(b, rel=1e-5)


def test_similar_to_cosine_positive(embed):
    a = embed.embed(["stable angina"], batch_size=1)[0]
    b = embed.embed(["unstable angina"], batch_size=1)[0]
    c = embed.embed(["how to fly a kite"], batch_size=1)[0]
    sim_ab = _cos(a, b)
    sim_ac = _cos(a, c)
    assert sim_ab > sim_ac


def _cos(x, y) -> float:
    return sum(i * j for i, j in zip(x, y))