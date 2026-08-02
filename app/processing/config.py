"""Batching / throughput config for the processing layer."""
from __future__ import annotations

import os

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessingConfig:
    # parallelism
    concurrency: int = min(16, (os.cpu_count() or 4) + 1)   # worker pool size
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
    embed_batch_size: int = 64    # row-batch for embedding calls (seam)

    def snapshot(self) -> dict:
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}