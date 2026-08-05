"""Configuration for the chunking module.

Immutable, versioned, snapshot-able. Every tunable (size band, overlap,
token-budget batching caps) lives here so chunking is reproducible given
(DOM, config, tokenizer) — the same trust property the parser and normalizer
configs carry.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkingConfig:
    """Immutable chunking parameters. Snapshot into ``ChunkProvenance``."""

    # identity / lineage
    chunker_version: str = "chunker-v0.1.0"
    dom_schema_version: str = "dom-schema-v0.1.0"

    # size policy (architecture §3.3): retrieval atom ~400 tokens, band 256-768,
    # absolute cap 2048 (well under BGE-M3's 8194 ceiling -> no silent truncation).
    target_tokens: int = 400
    min_band_tokens: int = 256
    soft_max_tokens: int = 768
    hard_max_tokens: int = 2048

    # overlap: ~10%, sentence-aligned, ONLY at heading seams (attributed).
    overlap_tokens: int = 48
    overlap_at_heading_seams: bool = True

    # token-budget batching (architecture §3.9): fp16 RTX 3050 4 GB envelope.
    max_tokens_per_call: int = 16384
    max_texts_per_call: int = 32

    # tokenizer: primary pinned BGE BPE; char/4 heuristic as a hermetic fallback.
    tokenizer_mode: str = "bge-m3"
    allow_char4_fallback: bool = True
    tokenizer_path: str = "models/bge-m3/tokenizer.json"

    def snapshot(self) -> dict:
        """A JSON-safe fingerprint of this config (for provenance)."""
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}
