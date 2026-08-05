"""Synthetic Data Factory — Semantic Chunking module (Module #3).

A decoupled projection that turns a **normalized DOM** into content-addressed,
lineage-carrying chunks — the retrieval atom — and projects them to embeddings
through the existing `Embedder` protocol. It is a *consumer of the DOM*, never
a parser/normalizer stage: the DOM is the single source of truth; everything
downstream is a projection.

Trust boundary (same as the rest of the platform):
  * idempotent     — content-addressed `chunk_id`; same-version artifacts are
                     deterministic overwrites; embeddings are never written twice.
  * deterministic  — the chunker is a pure function of (DOM, config, tokenizer);
                     token counts come from one pinned counter instance.
  * faithful       — `text` is a join of real `Block.text`; empty/None blocks are
                     skipped and reported, never fabricated.
  * on-prem        — the BGE tokenizer + model live under `models/`; events go to
                     a silent sink in batch contexts.

Dependencies: `app.parser.dom` (Document), `app.parser.events`, and the
`app.embedding` `Embedder` protocol via `factory.default_embedder`. No
dependency on normalizer or processing internals.

Version: 0.1.0
"""

__version__ = "0.1.0"

from .batching import group_by_token_budget
from .chunker import ChunkResult, SemanticChunker
from .config import ChunkingConfig
from .pipeline import ChunkEmbedPipeline, ChunkEmbedResult
from .schema import Chunk, ChunkProvenance, ChunksArtifact
from .store import ChunkStore, FilesystemChunkStore
from .tokenize import TokenCounter

__all__ = [
    "Chunk", "ChunkProvenance", "ChunksArtifact", "ChunkResult",
    "ChunkingConfig", "TokenCounter", "SemanticChunker",
    "group_by_token_budget", "ChunkStore", "FilesystemChunkStore",
    "ChunkEmbedPipeline", "ChunkEmbedResult",
]
