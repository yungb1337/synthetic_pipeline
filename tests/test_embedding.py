"""Tests for the batching-capable embedding seam (Module #4 seam)."""
from __future__ import annotations

from app.embedding.dummy import DummyEmbedder
from app.embedding.runner import batch_embed, embed_document_blocks
from app.parser.dom import Block, Document, Metadata, Page, Provenance


def _block(seq: int, text: str) -> Block:
    return Block(id=f"d1/b00_{seq:04d}", text=text, page=0)


def _doc(n: int) -> Document:
    blocks = [_block(i, f"block {i} of the document") for i in range(n)]
    return Document(
        version="v1", document_id="x", source_hash="0", metadata=Metadata(),
        provenance=Provenance(parser_version="p", dom_schema_version="v1"),
        reading_order=[b.id for b in blocks], pages=[Page(index=0, blocks=blocks)],
    )


def test_dummy_deterministic():
    e = DummyEmbedder(dim=128)
    assert e.embed(["hello"], 8)[0] == e.embed(["hello"], 8)[0]
    # fixed dimension
    assert len(e.embed(["a"], 8)[0]) == 128


def test_batch_embed_shape_and_order():
    e = DummyEmbedder(dim=32)
    texts = [f"text number {i}" for i in range(17)]
    out = batch_embed(e.embed, texts, batch_size=5)
    assert len(out) == 17
    assert all(len(v) == 32 for v in out)
    # deterministic ordering retained
    assert out[3] == e.embed([texts[3]], 5)[0]


def test_embed_document_blocks():
    e = DummyEmbedder(dim=16)
    doc = _doc(4)
    m = embed_document_blocks(e.embed, doc, batch_size=2)
    assert len(m) == 4
    assert set(m.keys()) == {b.id for p in doc.pages for b in p.blocks}