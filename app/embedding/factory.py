"""Choose the best available embedder for this machine.

Policy:
  1. If a real local model (sentence-transformers + torch) is present and
     `force_real` is not disabled, use it (GPU-aware).
  2. Otherwise fall back to the deterministic `DummyEmbedder` so the pipeline
     still runs (CI, machines without torch) and tests stay hermetic.

The model downloads to the local HF cache on first use; it's still fully local
and the larger `Embedder` contract (list-in -> vectors-out, batched) is met.
"""
from __future__ import annotations

from dataclasses import dataclass

from .dummy import DummyEmbedder
from .embedder import Embedder
from .sbert import SentenceTransformerEmbedder


@dataclass(frozen=True)
class EmbeddingOptions:
    model: str = "BAAI/bge-m3"    # 1024-dim, multilingual; loads from models/bge-m3
    device: str = "auto"          # "auto" | "cuda" | "cpu"
    batch_size: int = 128
    fp16: bool = True             # lower VRAM on GPU (fits RTX 3050 4GB)
    real_if_available: bool = True


def default_embedder(opts: EmbeddingOptions | None = None) -> Embedder:
    opts = opts or EmbeddingOptions()
    if opts.real_if_available:
        try:
            s = SentenceTransformerEmbedder(model=opts.model, device=opts.device,
                                            batch_size=opts.batch_size, fp16=opts.fp16)
            if s.ok:
                return s
        except Exception:
            pass
    return DummyEmbedder()