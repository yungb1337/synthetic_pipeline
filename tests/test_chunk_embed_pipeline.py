"""Hermetic tests for ChunkEmbedPipeline (DummyEmbedder only)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from app.chunking import ChunkEmbedPipeline
from app.embedding.dummy import DummyEmbedder
from app.parser.dom import Block, Document, Metadata, Page, Provenance

DID = "d-pipe"


def _block(seq: int, text: str, kind: str = "paragraph") -> Block:
    return Block(id=f"{DID}/b{seq:03d}", text=text, kind=kind, page=0)


def _write_norm_dom(root: str, doc_id: str, blocks, version: str = "norm-v0.1.0",
                    normalizer_version: str = "normalizer-v0.1.0") -> Document:
    doc = Document(
        version="dom-schema-v0.1.0", document_id=doc_id, source_hash="00", metadata=Metadata(),
        provenance=Provenance(parser_version="p", dom_schema_version="dom-schema-v0.1.0",
                              normalizer_version=normalizer_version),
        reading_order=[b.id for b in blocks],
        pages=[Page(index=0, blocks=blocks)],
    )
    d = Path(root) / "dom" / doc_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{version}.docJSON").write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    return doc


def _store(tmp_path) -> str:
    return str(tmp_path / "store")


def _pipe(tmp_path, embedder=None, **kw):
    return ChunkEmbedPipeline(store_root=_store(tmp_path), embedder=embedder or DummyEmbedder(), **kw)


# --------------------------------------------------------------- run behavior
def test_embeds_all_on_first_run(tmp_path):
    blocks = [_block(0, "x" * 2000), _block(1, "x" * 2000)]  # each its own chunk
    _write_norm_dom(_store(tmp_path), DID, blocks)
    r = _pipe(tmp_path).run(DID)
    assert r.status == "ok"
    assert r.chunks_created == 2
    assert r.embedded == 2
    assert r.skipped == 0
    store = _pipe(tmp_path).chunk_store
    got = store.get_embeddings(DID, "chunker-v0.1.0", "dummy-feature-hash")
    assert got is not None
    assert len(got[1]) == 2


def test_never_embeds_twice(tmp_path):
    blocks = [_block(0, "x" * 2000), _block(1, "x" * 2000)]
    _write_norm_dom(_store(tmp_path), DID, blocks)
    p = _pipe(tmp_path)
    first = p.run(DID)
    sidecar = tmp_path / "store" / "embeddings" / DID / "emb-v0.1.0-dummy-feature-hash.json"
    before = sidecar.read_bytes()
    second = p.run(DID)
    assert first.embedded == 2 and first.skipped == 0
    assert second.embedded == 0 and second.skipped == 2
    assert sidecar.read_bytes() == before   # no write happened -> identical bytes


def test_new_chunk_only_embedded(tmp_path):
    blocks = [_block(0, "x" * 2000), _block(1, "x" * 2000)]
    _write_norm_dom(_store(tmp_path), DID, blocks)
    p = _pipe(tmp_path)
    p.run(DID)
    # append one more large block -> one new chunk_id; existing chunks unchanged
    blocks.append(_block(2, "x" * 2000))
    _write_norm_dom(_store(tmp_path), DID, blocks)
    r = p.run(DID)
    assert r.embedded == 1
    assert r.skipped == 2


def test_artifact_shapes(tmp_path):
    blocks = [_block(0, "x" * 2000), _block(1, "x" * 2000)]
    _write_norm_dom(_store(tmp_path), DID, blocks)
    p = _pipe(tmp_path)
    p.run(DID)
    store = p.chunk_store
    chunks = store.latest_chunks(DID)
    chunk_ids = [c.chunk_id for c in chunks.chunks]
    got = store.get_embeddings(DID, "chunker-v0.1.0", "dummy-feature-hash")
    assert got is not None
    got_ids, matrix, _ = got
    assert got_ids == chunk_ids
    assert matrix.shape == (2, 64)                     # DummyEmbedder dim=64
    assert matrix.dtype == np.float32
    for i, cid in enumerate(chunk_ids):
        row = store.get_embedding(DID, cid, "chunker-v0.1.0", "dummy-feature-hash")
        assert row is not None and list(matrix[i]) == row


def test_embedding_ref_populated(tmp_path):
    blocks = [_block(0, "x" * 2000), _block(1, "x" * 2000)]
    _write_norm_dom(_store(tmp_path), DID, blocks)
    p = _pipe(tmp_path)
    p.run(DID)
    chunks = p.chunk_store.latest_chunks(DID)
    assert chunks.chunks[0].embedding_ref.endswith("emb-v0.1.0-dummy-feature-hash.json")
    # chunk_id is content-addressed: unchanged by the embedding_ref rewrite
    got = p.chunk_store.get_embeddings(DID, "chunker-v0.1.0", "dummy-feature-hash")
    assert got[0] == [c.chunk_id for c in chunks.chunks]


def test_event_emitted(tmp_path):
    blocks = [_block(0, "x" * 2000)]
    _write_norm_dom(_store(tmp_path), DID, blocks)
    events = []
    p = _pipe(tmp_path, events=_CapturingPublisher(events))
    p.run(DID)
    names = [n for n, _ in events]
    assert "chunk_embedded.v1" in names
    payload = dict(events)["chunk_embedded.v1"]
    for key in ("doc_id", "chunker_version", "embedder_id", "chunks", "embedded", "skipped", "dim", "dtype", "ms"):
        assert key in payload
    assert payload["doc_id"] == DID
    assert payload["embedded"] == 1


def test_validation_stamp_in_sidecar(tmp_path):
    blocks = [_block(0, "x" * 2000)]
    _write_norm_dom(_store(tmp_path), DID, blocks)
    p = _pipe(tmp_path)
    p.run(DID)
    got = p.chunk_store.get_embeddings(DID, "chunker-v0.1.0", "dummy-feature-hash")
    assert got is not None
    val = got[2]["validation"]
    assert val["sample_chunk_id"]
    assert val["ok"] is True
    assert 0.0 <= val["cosine"] <= 1.0


def test_latest_norm_dom_resolved(tmp_path):
    blocks = [_block(0, "x" * 2000)]
    _write_norm_dom(_store(tmp_path), DID, blocks, version="norm-v0.1.0")
    _write_norm_dom(_store(tmp_path), DID, blocks, version="norm-v0.2.0", normalizer_version="normalizer-v0.2.0")
    p = _pipe(tmp_path)
    r = p.run(DID)
    assert r.status == "ok"
    assert r.dom_storage_key == f"dom/{DID}/norm-v0.2.0.docJSON"
    art = p.chunk_store.latest_chunks(DID)
    assert art.dom_storage_key == r.dom_storage_key
    assert art.chunks[0].provenance.normalizer_version == "normalizer-v0.2.0"


def test_missing_dom_fails_gracefully(tmp_path):
    p = _pipe(tmp_path)
    r = p.run(DID)
    assert r.status == "failed"
    assert r.error
    # no chunk files written on a missing DOM
    assert not (tmp_path / "store" / "chunks" / DID).exists()


def test_explicit_dom_key_override(tmp_path):
    blocks = [_block(0, "x" * 2000)]
    _write_norm_dom(_store(tmp_path), DID, blocks)
    key = f"dom/{DID}/norm-v0.1.0.docJSON"
    p = _pipe(tmp_path)
    r = p.run(DID, dom_storage_key=key)
    assert r.status == "ok"
    assert r.dom_storage_key == key


# ------------------------------------------------------------------- CLI tests
def test_cli_chunk_only(tmp_path, monkeypatch):
    _force_dummy(monkeypatch)
    blocks = [_block(0, "x" * 2000), _block(1, "x" * 2000)]
    _write_norm_dom(_store(tmp_path), DID, blocks)
    from app.chunking import cli
    rc = cli.main(["--doc", DID, "--store", _store(tmp_path)])
    assert rc == 0
    assert (tmp_path / "store" / "chunks" / DID / "chunks-v0.1.0.json").exists()
    assert not (tmp_path / "store" / "embeddings" / DID).exists()


def test_chunk_only_never_touches_embedder(tmp_path, monkeypatch):
    blocks = [_block(0, "x" * 2000)]
    _write_norm_dom(_store(tmp_path), DID, blocks)

    def _boom(*a, **k):
        raise AssertionError("default_embedder must not be constructed for chunk-only runs")

    monkeypatch.setattr("app.chunking.pipeline.default_embedder", _boom)
    p = ChunkEmbedPipeline(store_root=_store(tmp_path))
    r = p.chunk_only(DID)
    assert r.status == "ok"
    assert r.chunks_created == 1
    # embedder still lazily unresolved until explicitly accessed
    assert p._embedder is None


def test_cli_with_embed(tmp_path, monkeypatch):
    _force_dummy(monkeypatch)
    blocks = [_block(0, "x" * 2000), _block(1, "x" * 2000)]
    _write_norm_dom(_store(tmp_path), DID, blocks)
    from app.chunking import cli
    rc = cli.main(["--doc", DID, "--store", _store(tmp_path), "--embed"])
    assert rc == 0
    assert (tmp_path / "store" / "chunks" / DID / "chunks-v0.1.0.json").exists()
    assert (tmp_path / "store" / "embeddings" / DID / "emb-v0.1.0-dummy-feature-hash.json").exists()
    assert (tmp_path / "store" / "embeddings" / DID / "emb-v0.1.0-dummy-feature-hash.npy").exists()


def test_cli_missing_dom_exits_1(tmp_path, monkeypatch):
    _force_dummy(monkeypatch)
    from app.chunking import cli
    rc = cli.main(["--doc", "nope", "--store", _store(tmp_path)])
    assert rc == 1


def _force_dummy(monkeypatch):
    """Default embedder would load real BGE-M3 on this box; keep CLI tests hermetic."""
    monkeypatch.setattr("app.chunking.pipeline.default_embedder", lambda *a, **k: DummyEmbedder())


class _CapturingPublisher:
    def __init__(self, sink):
        self._sink = sink

    def emit(self, name: str, payload: dict) -> None:
        self._sink.append((name, payload))
