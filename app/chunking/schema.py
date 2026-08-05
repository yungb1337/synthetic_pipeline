"""Chunk schema + content-addressed ``chunk_id``.

A chunk is the retrieval atom: a faithful slice of a normalized DOM, pinned to
its source blocks by ``source_block_ids``, carrying full provenance so the
chain doc -> DOM -> block -> chunk -> embedding is reconstructible. ``chunk_id``
is a sha256 over canonical JSON of ``(doc_id, text, source_block_ids)`` plus —
only for pieces of one oversized block — a ``piece_index`` discriminator.
Content-addressed, never embedder-dependent.
"""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, Field


class ChunkProvenance(BaseModel):
    """How a chunk was produced (reproducibility + lineage)."""

    chunker_version: str
    chunker_params: dict
    dom_schema_version: str
    normalizer_version: str | None = None
    dom_storage_key: str = ""
    tokenizer: str                 # "bge-m3" | "char4" — exactly which counts came from
    tokenizer_ref_hash: str | None = None
    forced_split: bool = False     # True only when a single sentence exceeded hard_max


class Chunk(BaseModel):
    """One retrieval atom. ``text`` is a faithful join of real ``Block.text``."""

    chunk_id: str                  # content-addressed sha256 — NEVER position/embedder dependent
    doc_id: str
    seq: int                       # position in document order (stable for a given DOM version)
    kind: str                      # paragraph|heading|list_item|code|formula|caption|mixed
                                   # reserved (documented next step): table_atomic|figure_caption
    text: str
    source_block_ids: list[str] = Field(default_factory=list)  # block ids fully covered by this chunk
    overlap_source_chunk_id: str | None = None   # set only when head repeats a prior chunk's tail
    page: int = 0                  # first page touched
    pages: list[int] = Field(default_factory=list)
    heading_anchor: str = ""       # nearest preceding heading text; "" when none (metadata only, NOT embedded)
    parent_chunk_id: str | None = None           # RESERVED for parent-child retrieval (not built this run)
    token_count: int = 0
    char_count: int = 0
    tokenizer: str = ""
    order_source: str = "reading_order"          # "reading_order" | "page_order" | "orphan"
    provenance: ChunkProvenance
    embedding_ref: str = ""        # emb storage key; populated by the embed pass
    # reserved for atomic table/figure-caption chunks (land without a schema bump)
    source_table_ids: list[str] | None = None
    source_image_ids: list[str] | None = None


class ChunksArtifact(BaseModel):
    """The persisted chunking output for one doc_id × chunker version."""

    schema_version: str = "chunks-v1"
    doc_id: str
    chunker_version: str
    dom_storage_key: str = ""
    chunks: list[Chunk] = Field(default_factory=list)
    report: dict = Field(default_factory=dict)


def compute_chunk_id(
    doc_id: str,
    text: str,
    source_block_ids: list[str],
    piece_index: int | None = None,
) -> str:
    """Content-addressed chunk identity (ADR-009).

    Canonical JSON = sort_keys + UTF-8 + no whitespace -> identical bytes every
    run. Excludes ``seq``, ``heading_anchor``, ``chunker_version``,
    ``embedding_ref`` (re-order/version/metadata changes must not invalidate an
    embedding); includes ``source_block_ids`` so lineage pins to source bytes.

    ``piece_index`` discriminates pieces of ONE oversized block whose text is
    byte-identical (a >2048-token block of repeated identical sentences or a
    force-split degenerate sentence): without it those distinct pieces would
    share a ``chunk_id`` and break the never-embed-twice key and
    ``get_embedding``. Set only for sentence-split/force-split pieces; ordinary
    chunks omit it so their identity stays pure content + lineage.
    """
    identity = {"doc_id": doc_id, "text": text, "source_block_ids": source_block_ids}
    if piece_index is not None:
        identity["piece_index"] = piece_index
    payload = json.dumps(
        identity,
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
