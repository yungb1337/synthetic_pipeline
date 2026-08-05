"""Tests for the chunk store (versioned keys, round-trips, traversal, sanitize)."""
from __future__ import annotations

import numpy as np

from app.chunking.schema import Chunk, ChunkProvenance, ChunksArtifact
from app.chunking.store import FilesystemChunkStore, _sanitize_embedder_id


def _artifact(doc_id: str = "d1", chunker_version: str = "chunker-v0.1.0", n: int = 2) -> ChunksArtifact:
    chunks = []
    for i in range(n):
        prov = ChunkProvenance(
            chunker_version=chunker_version, chunker_params={},
            dom_schema_version="s", tokenizer="char4",
        )
        chunks.append(Chunk(
            chunk_id=f"id-{doc_id}-{i}", doc_id=doc_id, seq=i, kind="paragraph",
            text=f"chunk text {i}", source_block_ids=[f"b{i}"], provenance=prov,
            tokenizer="char4",
        ))
    return ChunksArtifact(doc_id=doc_id, chunker_version=chunker_version, chunks=chunks, report={})


def test_chunks_roundtrip(tmp_path):
    store = FilesystemChunkStore(str(tmp_path / "store"))
    art = _artifact()
    key = store.put_chunks("d1", art)
    assert key == "chunks/d1/chunks-v0.1.0.json"
    got = store.get_chunks("d1", "chunker-v0.1.0")
    assert got is not None and got == art


def test_versioned_keys(tmp_path):
    store = FilesystemChunkStore(str(tmp_path / "store"))
    store.put_chunks("d1", _artifact())
    store.put_embeddings(
        "d1", "chunker-v0.1.0", "dummy-feature-hash",
        ["a", "b"], np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype="float32"),
        meta={"dim": 2, "dtype": "float32"},
    )
    root = tmp_path / "store"
    assert (root / "chunks" / "d1" / "chunks-v0.1.0.json").exists()
    assert (root / "embeddings" / "d1" / "emb-v0.1.0-dummy-feature-hash.json").exists()
    assert (root / "embeddings" / "d1" / "emb-v0.1.0-dummy-feature-hash.npy").exists()


def test_latest_chunks_numeric_sort(tmp_path):
    store = FilesystemChunkStore(str(tmp_path / "store"))
    for ver in ("chunker-v0.1.0", "chunker-v0.10.0", "chunker-v1.2.3"):
        store.put_chunks("d1", _artifact(chunker_version=ver))
    latest = store.latest_chunks("d1")
    assert latest is not None
    assert latest.chunker_version == "chunker-v1.2.3"


def test_deterministic_overwrite(tmp_path):
    store = FilesystemChunkStore(str(tmp_path / "store"))
    art = _artifact()
    store.put_chunks("d1", art)
    p = tmp_path / "store" / "chunks" / "d1" / "chunks-v0.1.0.json"
    first = p.read_bytes()
    store.put_chunks("d1", art)                       # same artifact -> identical bytes
    assert p.read_bytes() == first
    assert store.get_chunks("d1", "chunker-v0.1.0") == art


def test_versions_retained(tmp_path):
    store = FilesystemChunkStore(str(tmp_path / "store"))
    store.put_chunks("d1", _artifact(chunker_version="chunker-v0.1.0"))
    store.put_chunks("d1", _artifact(chunker_version="chunker-v0.2.0"))
    old = store.get_chunks("d1", "chunker-v0.1.0")
    new = store.get_chunks("d1", "chunker-v0.2.0")
    assert old is not None and new is not None
    assert old.chunker_version == "chunker-v0.1.0"
    assert new.chunker_version == "chunker-v0.2.0"


def test_embeddings_roundtrip(tmp_path):
    store = FilesystemChunkStore(str(tmp_path / "store"))
    chunk_ids = ["id-0", "id-1"]
    matrix = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype="float32")
    key = store.put_embeddings(
        "d1", "chunker-v0.1.0", "dummy-feature-hash", chunk_ids, matrix,
        meta={"dim": 2, "dtype": "float32"},
    )
    assert key.endswith("emb-v0.1.0-dummy-feature-hash.json")
    got = store.get_embeddings("d1", "chunker-v0.1.0", "dummy-feature-hash")
    assert got is not None
    got_ids, got_mat, meta = got
    assert got_ids == chunk_ids
    assert (got_mat == matrix).all()
    assert meta["dim"] == 2
    row = store.get_embedding("d1", "id-1", "chunker-v0.1.0", "dummy-feature-hash")
    assert row == [0.0, 1.0]
    assert store.get_embedding("d1", "missing", "chunker-v0.1.0", "dummy-feature-hash") is None


def test_iter_traversal(tmp_path):
    store = FilesystemChunkStore(str(tmp_path / "store"))
    store.put_chunks("d2", _artifact(doc_id="d2"))
    store.put_chunks("d1", _artifact(doc_id="d1"))
    docs = [a.doc_id for a in store.iter_all_chunks()]
    assert docs == ["d1", "d2"]
    store.put_embeddings(
        "d2", "chunker-v0.1.0", "dummy-feature-hash",
        ["x"], np.asarray([[1.0]], dtype="float32"), meta={"dim": 1},
    )
    store.put_embeddings(
        "d1", "chunker-v0.1.0", "dummy-feature-hash",
        ["x"], np.asarray([[2.0]], dtype="float32"), meta={"dim": 1},
    )
    emb_docs = [d for d, *_ in store.iter_embeddings()]
    assert emb_docs == ["d1", "d2"]


def test_sanitize_embedder_id():
    assert _sanitize_embedder_id("BAAI/bge-m3@local-fp16") == "BAAI__bge-m3_local-fp16"
    assert _sanitize_embedder_id("dummy-feature-hash") == "dummy-feature-hash"
    assert _sanitize_embedder_id("a b:c") == "a_b_c"
