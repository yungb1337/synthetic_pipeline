"""Token-budget batching for chunk embedding calls.

Greedy, order-preserving accumulation over the chunk sequence (embed row order
= chunk_id order = doc order). A group closes when adding the next chunk would
exceed ``max_tokens_per_call`` or the group would exceed ``max_texts_per_call``.
A chunk that alone exceeds a cap still gets its own group (never dropped).
"""
from __future__ import annotations

from .schema import Chunk
from .tokenize import TokenCounter


def group_by_token_budget(
    chunks: list[Chunk],
    counter: TokenCounter,
    max_tokens_per_call: int = 16384,
    max_texts_per_call: int = 32,
) -> list[list[Chunk]]:
    """Partition ``chunks`` into embed call groups respecting both caps.

    Uses each chunk's recorded ``token_count`` (same ``TokenCounter`` instance
    as chunk time — budget and recorded counts agree by construction); falls
    back to the counter only if a chunk lacks a recorded count. Empty input
    returns ``[]``.
    """
    if not chunks:
        return []
    groups: list[list[Chunk]] = []
    current: list[Chunk] = []
    tokens = 0
    for c in chunks:
        count = c.token_count if c.token_count else counter.count(c.text)
        if current and (tokens + count > max_tokens_per_call or len(current) >= max_texts_per_call):
            groups.append(current)
            current = []
            tokens = 0
        current.append(c)
        tokens += count
    if current:
        groups.append(current)
    return groups
