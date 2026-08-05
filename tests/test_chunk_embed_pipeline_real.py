"""Real BGE-M3 pipeline test — cosine-stable determinism (ADR-010).

Gated on sentence-transformers/torch + the local `models/bge-m3` copy, following
the `test_sbert_embedder.py` skip pattern. Verifies the ADR-010 guarantee
end-to-end: two independent pipeline runs over the same DOM yield chunk[0]
vectors with cosine >= 0.9999, dim 1024, stored float32.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.chunking import ChunkEmbedPipeline
from app.chunking.store import _sanitize_embedder_id
from app.embedding.factory import EmbeddingOptions, default_embedder
from app.parser.dom import Block, Document, Metadata, Page, Provenance

DID = "d-real"


def _available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        import torch  # noqa: F401
        return Path("models/bge-m3/tokenizer.json").exists() and Path("models/bge-m3/config.json").exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _available(), reason="sentence-transformers/torch/bge-m3 unavailable")


@pytest.fixture(scope="module")
def embedder():
    return default_embedder(EmbeddingOptions(real_if_available=True))


def _block(seq: int, text: str, kind: str = "paragraph") -> Block:
    return Block(id=f"{DID}/b{seq:03d}", text=text, kind=kind, page=0)


def _blocks():
    return [_block(0, "x" * 2000), _block(1, "x" * 2000)]  # each its own chunk


def _write_norm_dom(root: str, blocks) -> None:
    doc = Document(
        version="dom-schema-v0.1.0", document_id=DID, source_hash="00", metadata=Metadata(),
        provenance=Provenance(parser_version="p", dom_schema_version="dom-schema-v0.1.0",
                              normalizer_version="normalizer-v0.1.0"),
        reading_order=[b.id for b in blocks],
        pages=[Page(index=0, blocks=blocks)],
    )
    d = Path(root) / "dom" / DID
    d.mkdir(parents=True, exist_ok=True)
    (d / "norm-v0.1.0.docJSON").write_text(doc.model_dump_json(indent=2), encoding="utf-8")


def test_cosine_stable_across_runs(tmp_path, embedder):
    """ADR-010: two independent real-model passes are cosine-stable (>= 0.9999)."""
    roots = []
    for name in ("s1", "s2"):
        root = str(tmp_path / name)
        _write_norm_dom(root, _blocks())
        p = ChunkEmbedPipeline(store_root=root, embedder=embedder)
        r = p.run(DID)
        assert r.status == "ok"
        assert r.embedded == 2
        roots.append(p)
    eid = _sanitize_embedder_id(embedder.name)
    v1 = roots[0].chunk_store.get_embeddings(DID, "chunker-v0.1.0", eid)
    v2 = roots[1].chunk_store.get_embeddings(DID, "chunker-v0.1.0", eid)
    assert v1 is not None and v2 is not None
    cos = _cos(list(v1[1][0]), list(v2[1][0]))
    assert cos >= 0.9999


def test_dim_and_dtype(tmp_path, embedder):
    root = str(tmp_path / "s")
    _write_norm_dom(root, _blocks())
    p = ChunkEmbedPipeline(store_root=root, embedder=embedder)
    r = p.run(DID)
    assert r.status == "ok"
    assert r.dim == 1024
    assert r.dtype == "float32"
    eid = _sanitize_embedder_id(embedder.name)
    got = p.chunk_store.get_embeddings(DID, "chunker-v0.1.0", eid)
    assert got is not None
    assert got[1].dtype == np.float32
    assert got[1].shape == (2, 1024)


def test_name_identity(embedder):
    assert "bge-m3" in embedder.name
    assert "fp16" in embedder.name or "fp32" in embedder.name


def test_never_embeds_twice_real(tmp_path, embedder):
    root = str(tmp_path / "s")
    _write_norm_dom(root, _blocks())
    p = ChunkEmbedPipeline(store_root=root, embedder=embedder)
    first = p.run(DID)
    second = p.run(DID)
    assert first.status == "ok" and first.embedded == 2
    assert second.status == "ok" and second.embedded == 0 and second.skipped == 2


def _cos(x, y) -> float:
    import math

    dot = sum(a * b for a, b in zip(x, y))
    nx = math.sqrt(sum(a * a for a in x)) or 1.0
    ny = math.sqrt(sum(b * b for b in y)) or 1.0
    return dot / (nx * ny)
