"""Embedding module — batching-shaped (Module #4 seam, built early for scale).

This package defines the embedder CONTRACT and a batched runner so the rest of
the pipeline is written against batched model calls from day one. The concrete
model (a real GPU embedding model, e.g. BGE/e5/multimodal) is plugged in later
by implementing `Embedder`; `DummyEmbedder` keeps everything runnable +
deterministic for tests and small-scale demos today.
"""

__version__ = "0.1.0"

from .dummy import DummyEmbedder
from .embedder import Embedder
from .factory import EmbeddingOptions, default_embedder
from .runner import batch_embed, embed_document_blocks
from .sbert import SentenceTransformerEmbedder, cuda_available

__all__ = [
    "DummyEmbedder",
    "Embedder",
    "EmbeddingOptions",
    "SentenceTransformerEmbedder",
    "batch_embed",
    "cuda_available",
    "default_embedder",
    "embed_document_blocks",
]
