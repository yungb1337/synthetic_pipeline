"""Batched embedding runner.

* `batch_embed` slices a text list into model-sized batches so the model is
  called with BATCH inputs (never one-at-a-time), and validates dimensionality.
* `embed_document_blocks` embeds a normalized DOM's block texts in batched
  calls, returning `{block_id: vector}`.
"""
from __future__ import annotations

from typing import Callable

from ..parser.dom import Document


def batch_embed(
    embed_fn,                # Callable[[list[str]], list[list[float]]]  (may batch internally)
    texts: list[str],
    batch_size: int = 64,
) -> list[list[float]]:
    batch_size = max(1, batch_size)
    out: list[list[float]] = []
    dim: int | None = None
    for i in range(0, len(texts), batch_size):
        slice_texts = texts[i:i + batch_size]
        rows = embed_fn(slice_texts)
        if not rows:
            continue
        if dim is None:
            dim = len(rows[0])
        for row in rows:
            if dim is not None and len(row) != dim:   # shape guard
                raise ValueError(f"embedding dim mismatch: {len(row)} != {dim}")
            out.append(row)
    return out


def embed_document_blocks(
    embed_fn,
    doc: Document,
    batch_size: int = 64,
) -> dict[str, list[float]]:
    """Batch-embed every text block of a normalized DOM."""
    blocks = [b for p in doc.pages for b in p.blocks]
    texts = [b.text for b in blocks]
    ids = [b.id for b in blocks]
    vecs = batch_embed(embed_fn, texts, batch_size)
    return dict(zip(ids, vecs))