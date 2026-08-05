"""Tests for token-budget batching (both caps, order, over-budget isolation)."""
from __future__ import annotations

from app.chunking.batching import group_by_token_budget
from app.chunking.schema import Chunk, ChunkProvenance
from app.chunking.tokenize import TokenCounter

COUNTER = TokenCounter(mode="char4")


def _chunk(cid: str, tokens: int) -> Chunk:
    prov = ChunkProvenance(
        chunker_version="chunker-v0.1.0", chunker_params={},
        dom_schema_version="s", tokenizer="char4",
    )
    return Chunk(
        chunk_id=cid, doc_id="d1", seq=0, kind="paragraph", text="t" * tokens,
        source_block_ids=[cid], token_count=tokens, char_count=tokens,
        provenance=prov, tokenizer="char4",
    )


def test_respects_token_cap():
    chunks = [_chunk(f"c{i}", 100) for i in range(10)]
    groups = group_by_token_budget(chunks, COUNTER, max_tokens_per_call=250, max_texts_per_call=100)
    assert all(sum(c.token_count for c in g) <= 250 for g in groups)
    assert len(groups) == 5  # 100+100 stays; +100 would exceed 250


def test_respects_text_cap():
    chunks = [_chunk(f"c{i}", 1) for i in range(10)]
    groups = group_by_token_budget(chunks, COUNTER, max_tokens_per_call=1000, max_texts_per_call=3)
    assert all(len(g) <= 3 for g in groups)
    assert [len(g) for g in groups] == [3, 3, 3, 1]


def test_order_preserved():
    chunks = [_chunk(f"c{i}", 10) for i in range(7)]
    groups = group_by_token_budget(chunks, COUNTER, max_tokens_per_call=25, max_texts_per_call=3)
    flat = [c.chunk_id for g in groups for c in g]
    assert flat == [f"c{i}" for i in range(7)]


def test_single_over_budget_chunk_own_group():
    chunks = [_chunk("big", 500), _chunk("small", 10)]
    groups = group_by_token_budget(chunks, COUNTER, max_tokens_per_call=100, max_texts_per_call=32)
    assert len(groups) == 2
    assert groups[0][0].chunk_id == "big"
    assert groups[1][0].chunk_id == "small"


def test_exact_boundary():
    chunks = [_chunk(f"c{i}", 50) for i in range(4)]
    groups = group_by_token_budget(chunks, COUNTER, max_tokens_per_call=100, max_texts_per_call=32)
    assert [len(g) for g in groups] == [2, 2]   # 50+50 == cap stays in-group


def test_empty_input():
    assert group_by_token_budget([], COUNTER) == []
