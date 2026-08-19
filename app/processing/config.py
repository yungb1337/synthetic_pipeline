"""Batching / throughput config for the processing layer."""
from __future__ import annotations

import os

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessingConfig:
    # parallelism
    concurrency: int = min(16, (os.cpu_count() or 4) + 1)   # worker pool size
    # Page-centric engine (ADR-013): native pool is wide; heavy (Docling) pool is
    # bounded by measured RAM. None => auto-derived by ResourceGovernor. Pass an
    # explicit int to override (e.g. --heavy-concurrency 2 on a small box).
    native_concurrency: int | None = None
    heavy_concurrency: int | None = None
    # file discovery
    exts: tuple[str, ...] = (
        ".pdf", ".docx", ".xlsx", ".csv", ".tsv", ".json", ".xml",
        ".html", ".md", ".markdown", ".txt", ".png", ".jpg", ".jpeg", ".tiff", ".gif",
    )
    # retries
    max_retries: int = 3
    base_backoff_s: float = 1.0
    # idempotent incremental run
    manifest_path: str = "work/manifest.json"
    # batching of model-boundary calls
    ocr_warm: bool = True         # preload OCR engine once before the pool
    embed_batch_size: int = 32    # row-batch for embedding calls (seam); fp16 envelope on the
                                  # 4 GB RTX 3050 (B≈32 @ L=1024 is near-OOM) — the chunk pipeline
                                  # passes its own token-budget caps and never inherits this

    def snapshot(self) -> dict:
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}