"""Tests for Module #3 — semantic chunking (config, schema, tokenize, sentences, chunker)."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.chunking import (
    Chunk,
    ChunkingConfig,
    ChunkProvenance,
    ChunksArtifact,
    SemanticChunker,
    TokenCounter,
)
from app.chunking.schema import compute_chunk_id
from app.chunking.sentences import split_sentences, tail_sentences
from app.parser.dom import Block, Document, ImageObject, Metadata, Page, Provenance, Table

BGE_TOKENIZER = Path("models/bge-m3/tokenizer.json")


# ----------------------------------------------------------------- test helpers
def _block(seq: int, text: str, kind: str = "paragraph", page: int = 0) -> Block:
    return Block(id=f"d1/b00_{seq:04d}", text=text, kind=kind, page=page)


def _doc(blocks, reading_order=None, provenance=None, document_id="d-test") -> Document:
    ro = reading_order if reading_order is not None else [b.id for b in blocks]
    prov = provenance or Provenance(
        parser_version="parser-v0.1.0",
        dom_schema_version="dom-schema-v0.1.0",
        normalizer_version="normalizer-v0.1.0",
    )
    return Document(
        version="dom-schema-v0.1.0",
        document_id=document_id,
        source_hash="00",
        metadata=Metadata(),
        provenance=prov,
        reading_order=ro,
        pages=[Page(index=0, blocks=blocks)],
    )


def _chunker(config=None, counter=None) -> SemanticChunker:
    cfg = config or ChunkingConfig()
    return SemanticChunker(cfg, counter or TokenCounter(mode="char4"))


def _chunk_with(seq: int = 0, anchor: str = "", version: str = "chunker-v0.1.0") -> Chunk:
    text = "the text"
    cid = compute_chunk_id("d1", text, ["b1"])
    prov = ChunkProvenance(
        chunker_version=version, chunker_params={}, dom_schema_version="s",
        normalizer_version=None, tokenizer="char4",
    )
    return Chunk(
        chunk_id=cid, doc_id="d1", seq=seq, kind="paragraph", text=text,
        source_block_ids=["b1"], heading_anchor=anchor, provenance=prov, tokenizer="char4",
    )


# --------------------------------------------------------------------- config
def test_config_defaults():
    cfg = ChunkingConfig()
    assert cfg.chunker_version == "chunker-v0.1.0"
    assert cfg.dom_schema_version == "dom-schema-v0.1.0"
    assert cfg.target_tokens == 400
    assert cfg.min_band_tokens == 256
    assert cfg.soft_max_tokens == 768
    assert cfg.hard_max_tokens == 2048
    assert cfg.overlap_tokens == 48
    assert cfg.overlap_at_heading_seams is True
    assert cfg.max_tokens_per_call == 16384
    assert cfg.max_texts_per_call == 32
    assert cfg.tokenizer_mode == "bge-m3"
    assert cfg.allow_char4_fallback is True
    assert cfg.tokenizer_path == "models/bge-m3/tokenizer.json"


def test_config_snapshot():
    snap = ChunkingConfig().snapshot()
    json.dumps(snap)  # JSON-safe
    assert snap["chunker_version"] == "chunker-v0.1.0"
    assert set(snap) == {
        "chunker_version", "dom_schema_version", "target_tokens", "min_band_tokens",
        "soft_max_tokens", "hard_max_tokens", "overlap_tokens",
        "overlap_at_heading_seams", "max_tokens_per_call", "max_texts_per_call",
        "tokenizer_mode", "allow_char4_fallback", "tokenizer_path",
    }


# --------------------------------------------------------------------- schema
def test_chunk_id_stable():
    a = compute_chunk_id("d1", "same text", ["b1", "b2"])
    b = compute_chunk_id("d1", "same text", ["b1", "b2"])
    assert a == b
    # seq / heading_anchor / chunker_version / embedding_ref are NOT part of identity
    c1 = _chunk_with(seq=0, anchor="")
    c2 = _chunk_with(seq=5, anchor="Chapter 1", version="chunker-v9.9.9")
    assert c1.chunk_id == c2.chunk_id


def test_chunk_id_changes_with_text_or_blocks():
    base = compute_chunk_id("d1", "text", ["b1"])
    assert compute_chunk_id("d1", "textX", ["b1"]) != base
    assert compute_chunk_id("d1", "text", ["b2"]) != base
    assert compute_chunk_id("d2", "text", ["b1"]) != base


def test_schema_roundtrip():
    prov = ChunkProvenance(
        chunker_version="chunker-v0.1.0", chunker_params={"a": 1},
        dom_schema_version="s", tokenizer="char4",
    )
    chunk = Chunk(
        chunk_id=compute_chunk_id("d1", "hello", ["b1"]), doc_id="d1", seq=0,
        kind="paragraph", text="hello", source_block_ids=["b1"], provenance=prov,
        tokenizer="char4",
    )
    art = ChunksArtifact(doc_id="d1", chunker_version="chunker-v0.1.0", chunks=[chunk], report={"r": 1})
    art2 = ChunksArtifact.model_validate_json(art.model_dump_json())
    assert art2 == art
    assert art2.schema_version == "chunks-v1"


def test_reserved_fields_present():
    prov = ChunkProvenance(
        chunker_version="chunker-v0.1.0", chunker_params={}, dom_schema_version="s", tokenizer="char4",
    )
    chunk = Chunk(
        chunk_id=compute_chunk_id("d1", "atomic table", ["b9"]), doc_id="d1", seq=0,
        kind="table_atomic", text="atomic table", source_block_ids=["b9"],
        parent_chunk_id="p-1", source_table_ids=["t1"], source_image_ids=["i1"],
        provenance=prov, tokenizer="char4",
    )
    art = ChunksArtifact(doc_id="d1", chunker_version="chunker-v0.1.0", chunks=[chunk])
    art2 = ChunksArtifact.model_validate_json(art.model_dump_json())
    assert art2.chunks[0].kind == "table_atomic"
    assert art2.chunks[0].parent_chunk_id == "p-1"
    assert art2.chunks[0].source_table_ids == ["t1"]
    assert art2.chunks[0].source_image_ids == ["i1"]


# ------------------------------------------------------------------ tokenize
def test_char4_deterministic():
    c = TokenCounter(mode="char4")
    t = "hello world this is a test string"
    assert c.tokenizer == "char4"
    assert c.count(t) == max(1, len(t) // 4)
    assert c.count(t) == c.count(t)
    assert c.tokenizer_ref_hash is None


def test_char4_fallback_on_missing_file():
    c = TokenCounter(mode="bge-m3", tokenizer_path="does/not/exist.json", allow_char4_fallback=True)
    assert c.tokenizer == "char4"
    assert c.count("abc") == 1  # max(1, 3 // 4)


def test_bge_mode_when_available():
    if not BGE_TOKENIZER.exists():
        pytest.skip("models/bge-m3/tokenizer.json absent")
    c = TokenCounter(mode="bge-m3")
    assert c.tokenizer == "bge-m3"
    assert len(c.tokenizer_ref_hash) == 64
    short = "hello world"
    long = (short + " ") * 10
    assert c.count(long) > c.count(short)
    assert c.count(short) == c.count(short)


def test_count_deterministic():
    t = "The quick brown fox jumps over the lazy dog."
    for mode in ("char4", "bge-m3"):
        c = TokenCounter(mode=mode)
        assert c.count(t) == c.count(t)


# ------------------------------------------------------------------ sentences
def test_split_basic():
    s, amb = split_sentences("The cat sat down. The dog ran away.")
    assert s == ["The cat sat down.", "The dog ran away."]
    assert amb == 0


def test_abbreviation_guard():
    s, amb = split_sentences("Dr. Smith took aspirin. It helped.")
    assert s == ["Dr. Smith took aspirin.", "It helped."]
    assert amb > 0
    s2, amb2 = split_sentences("Use e.g. aspirin daily.")
    assert s2 == ["Use e.g. aspirin daily."]
    assert amb2 > 0
    s3, amb3 = split_sentences("U.S. guidelines were followed.")
    assert s3 == ["U.S. guidelines were followed."]
    assert amb3 > 0


def test_decimal_false_split_guard():
    # alphanumeric tokens with a trailing period are NOT sentence boundaries
    s, amb = split_sentences("BP120/80. The patient rested.")
    assert s == ["BP120/80. The patient rested."]
    assert amb > 0
    s2, amb2 = split_sentences("Version2.0. This release shipped.")
    assert s2 == ["Version2.0. This release shipped."]
    assert amb2 > 0


def test_pure_number_sentence_end_still_splits():
    # a real sentence ending in a plain number is still a boundary
    s, _ = split_sentences("The dose is 45. He improved.")
    assert s == ["The dose is 45.", "He improved."]


def test_split_cjk_and_ellipsis():
    s, _ = split_sentences("他说完了。然后他走了。")
    assert s == ["他说完了。", "然后他走了。"]
    s2, _ = split_sentences("他说完了！ 然后他走了。")
    assert s2 == ["他说完了！", "然后他走了。"]
    s3, _ = split_sentences("She hesitated… Then she spoke.")
    assert s3 == ["She hesitated…", "Then she spoke."]


def test_sentences_deterministic():
    t = "Dr. Smith ran. e.g. aspirin helped. 他说完了。"
    assert split_sentences(t) == split_sentences(t)


def test_tail_sentences_budget():
    c = TokenCounter(mode="char4")
    text = "First sentence. Second sentence. Third sentence."
    tail = tail_sentences(text, c, budget_tokens=1)   # any one sentence > 1 token
    assert tail == ["Third sentence."]
    assert tail_sentences(text, c, budget_tokens=100) == [
        "First sentence.", "Second sentence.", "Third sentence.",
    ]
    assert tail_sentences(text, c, budget_tokens=1) == tail_sentences(text, c, budget_tokens=1)


# ------------------------------------------------------------------- chunker
def test_heading_starts_chunk():
    blocks = [_block(0, "Intro text here.", kind="paragraph"),
              _block(1, "Section", kind="heading"),
              _block(2, "x" * 4000)]  # 1000 tokens: too big to merge into the heading
    r = _chunker().chunk(_doc(blocks))
    assert len(r.chunks) == 3
    assert [c.kind for c in r.chunks] == ["paragraph", "heading", "paragraph"]
    # a heading is the anchor of its section, never the tail of the previous chunk
    assert r.chunks[1].source_block_ids == [blocks[1].id]
    assert r.chunks[0].heading_anchor == ""


def test_merge_to_target():
    blocks = [_block(i, "x" * 150) for i in range(8)]  # 8 x 150 chars ≈ 301 tokens joined
    r = _chunker().chunk(_doc(blocks))
    assert len(r.chunks) == 1
    c = r.chunks[0]
    assert c.kind == "mixed"
    assert c.text == "\n".join(b.text for b in blocks)
    assert c.token_count <= 400


def test_band_merge():
    blocks = [_block(0, "y" * 600), _block(1, "y" * 2000), _block(2, "y" * 2000)]
    r = _chunker().chunk(_doc(blocks))
    assert len(r.chunks) == 2
    assert r.chunks[0].kind == "mixed"                 # 600+2000 chars merged via band rule
    assert r.chunks[0].source_block_ids == [blocks[0].id, blocks[1].id]
    assert r.chunks[1].source_block_ids == [blocks[2].id]


def test_empty_blocks_skipped_and_reported():
    blocks = [_block(0, "real text"), _block(1, "   "), _block(2, "")]
    r = _chunker().chunk(_doc(blocks))
    assert len(r.chunks) == 1
    assert r.chunks[0].text == "real text"
    assert r.report["blocks_skipped_empty"] == 2


def test_orphan_appended_and_flagged():
    a, b = _block(0, "x" * 1000), _block(1, "x" * 1000)
    orphan = _block(2, "y" * 200)
    doc = _doc([a, b, orphan], reading_order=[a.id, b.id])
    r = _chunker().chunk(doc)
    assert r.report["blocks_orphaned"] == 1
    assert r.report["order_source_used"] == "reading_order"
    assert r.chunks[-1].order_source == "orphan"
    assert r.chunks[-1].source_block_ids == [orphan.id]


def test_reading_order_empty_falls_back_page_order():
    # large blocks so each is its own chunk (page order is observable)
    b1, b2 = _block(0, "x" * 2000), _block(1, "x" * 2000)
    doc = Document(
        version="dom-schema-v0.1.0", document_id="d-test", source_hash="00", metadata=Metadata(),
        provenance=Provenance(parser_version="p", dom_schema_version="s"),
        reading_order=[],  # empty -> page-order fallback
        pages=[Page(index=1, blocks=[b2]), Page(index=0, blocks=[b1])],
    )
    r = _chunker().chunk(doc)
    assert r.report["order_source_used"] == "page_order"
    assert [c.source_block_ids[0] for c in r.chunks] == [b1.id, b2.id]
    assert all(c.order_source == "page_order" for c in r.chunks)


def test_oversized_block_sentence_split():
    sentence = "The patient continued treatment without complications."
    big = _block(1, " ".join([sentence] * 170))   # > 8192 chars -> > 2048 tokens (char4)
    blocks = [_block(0, "Introduction", kind="heading"), big]
    r = _chunker().chunk(_doc(blocks))
    oversized = r.chunks[1:]                       # skip the heading chunk
    assert len(oversized) >= 2
    for c in oversized:
        assert c.token_count <= 400
        assert c.source_block_ids == [big.id]
        assert c.provenance.forced_split is False
        assert c.heading_anchor == "Introduction"
        assert c.kind == "paragraph"


def test_forced_split_single_huge_sentence():
    big = _block(0, "This is fine. " + "X" * 9000 + ".")
    r = _chunker().chunk(_doc([big]))
    forced = [c for c in r.chunks if c.provenance.forced_split]
    normal = [c for c in r.chunks if not c.provenance.forced_split]
    assert forced, "expected at least one forced-split sub-chunk"
    assert all(c.token_count <= 2048 for c in forced)
    assert r.report["forced_splits"] == len(forced)
    assert [c.text for c in normal] == ["This is fine."]


def test_oversized_repeated_sentences_distinct_chunk_ids():
    # one >hard_max block of byte-identical sentences: without the piece
    # discriminator every sentence-sub-chunk would share a single chunk_id,
    # breaking the never-embed-twice key and get_embedding.
    sentence = "The patient continued treatment without complications."
    big = _block(0, " ".join([sentence] * 170))   # > 2048 tokens (char4)
    r = _chunker().chunk(_doc([big]))
    ids = [c.chunk_id for c in r.chunks]
    assert len(ids) >= 2
    assert len(ids) == len(set(ids)), "byte-identical oversized pieces must stay distinct"
    texts = [c.text for c in r.chunks]
    assert len(texts) > len(set(texts)), "precondition: pieces really are byte-identical"


def test_forced_split_identical_pieces_distinct_chunk_ids():
    # pathological: one degenerate sentence force-split into byte-identical
    # halves ("Y"*8400 -> "Y"*4200 twice) must not collide on chunk_id.
    big = _block(0, "Y" * 8400)
    r = _chunker().chunk(_doc([big]))
    forced = [c for c in r.chunks if c.provenance.forced_split]
    ids = [c.chunk_id for c in r.chunks]
    assert len(forced) >= 2
    assert forced[0].text == forced[1].text, "precondition: halves are byte-identical"
    assert len(ids) == len(set(ids)), "identical force-split pieces must stay distinct"


def test_overlap_only_at_heading_seams():
    blocks = [
        _block(0, "The patient had a stable recovery. He returned to work."),
        _block(1, "Follow-up", kind="heading"),
        _block(2, "The doctor recommended monthly visits."),
    ]
    r = _chunker().chunk(_doc(blocks))
    heading = r.chunks[1]
    assert heading.overlap_source_chunk_id == r.chunks[0].chunk_id
    assert heading.text.startswith("The patient had a stable recovery.")
    # overlap span is attributed, NOT in source_block_ids
    assert heading.source_block_ids == [blocks[1].id, blocks[2].id]
    # budget-bounded: ~48 tokens (192 chars in char4)
    overlap = heading.text.split("\n", 1)[0]
    assert len(overlap) <= 192
    # ordinary budget cuts get no overlap
    assert r.chunks[0].overlap_source_chunk_id is None


def test_overlap_disabled_via_config():
    cfg = replace(ChunkingConfig(), overlap_at_heading_seams=False)
    blocks = [
        _block(0, "The patient had a stable recovery. He returned to work."),
        _block(1, "Follow-up", kind="heading"),
    ]
    # disabled config -> no overlap even at a heading seam
    cfg_r = SemanticChunker(cfg, TokenCounter(mode="char4")).chunk(_doc(blocks))
    assert cfg_r.chunks[1].overlap_source_chunk_id is None
    assert not cfg_r.chunks[1].text.startswith("The patient")


def test_determinism_two_runs_identical():
    blocks = [
        _block(0, "Intro", kind="heading"),
        _block(1, "Some body text here. More sentences follow."),
        _block(2, "x" * 300),
        _block(3, "Deep Dive", kind="heading"),
        _block(4, "Final paragraph content."),
    ]
    doc = _doc(blocks)
    r1 = _chunker().chunk(doc)
    r2 = _chunker().chunk(doc)
    assert r1 == r2
    assert json.dumps([c.model_dump() for c in r1.chunks], sort_keys=True) == \
        json.dumps([c.model_dump() for c in r2.chunks], sort_keys=True)


def test_provenance_fields():
    blocks = [_block(0, "text")] + [_block(1, "y" * 300)]
    doc = _doc(blocks)
    counter = TokenCounter(mode="char4")
    cfg = ChunkingConfig()
    r = SemanticChunker(cfg, counter).chunk(doc, dom_storage_key="dom/d-test/norm-v0.1.0.docJSON")
    c = r.chunks[0]
    assert c.provenance.normalizer_version == "normalizer-v0.1.0"
    assert c.provenance.tokenizer == "char4"
    assert c.provenance.tokenizer_ref_hash is None
    assert c.provenance.chunker_params == cfg.snapshot()
    assert c.provenance.dom_storage_key == "dom/d-test/norm-v0.1.0.docJSON"
    assert c.tokenizer == "char4"
    assert r.dom_storage_key == "dom/d-test/norm-v0.1.0.docJSON"


def test_tables_untouched():
    blocks = [_block(0, "The body text."), _block(1, "More text.")]
    doc = _doc(blocks)
    doc.pages[0].tables.append(Table(id="t1", header=["h"], rows=[], page=0))
    doc.pages[0].images.append(ImageObject(id="img1", page=0))
    r = _chunker().chunk(doc)
    for c in r.chunks:
        assert all(bid.startswith("d1/b00_") for bid in c.source_block_ids)
    assert r.report["blocks_seen"] == 2
