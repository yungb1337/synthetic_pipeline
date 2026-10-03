"""Batching / throughput config for the processing layer."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessingConfig:
    # parallelism
    concurrency: int = min(16, (os.cpu_count() or 4) + 1)  # worker pool size
    # A4: cap the batch-layer concurrency so batch threads can never outnumber
    # the heavy (Docling) pool's capacity. Today `concurrency` is 17 on this
    # box while the heavy pool is RAM-capped at 4 workers — so up to 17 batch
    # threads can be blocked simultaneously on docling pages, starving the
    # native pool and inflating peak RSS (+72% vs baseline). Capping at
    # `heavy_concurrency * 2` keeps enough batch threads to saturate the heavy
    # pool without queuing docling work behind an unbounded thread count.
    # 0 / None = disabled (legacy behaviour: `concurrency` used as-is).
    concurrency_cap_heavy: int = 2
    # Page-centric engine (ADR-013): native pool is wide; heavy (Docling) pool is
    # bounded by measured RAM. None => auto-derived by ResourceGovernor. Pass an
    # explicit int to override (e.g. --heavy-concurrency 2 on a small box).
    native_concurrency: int | None = None
    heavy_concurrency: int | None = None
    # file discovery
    exts: tuple[str, ...] = (
        ".pdf",
        ".docx",
        ".xlsx",
        ".csv",
        ".tsv",
        ".json",
        ".xml",
        ".html",
        ".md",
        ".markdown",
        ".txt",
        ".png",
        ".jpg",
        ".jpeg",
        ".tiff",
        ".gif",
    )
    # retries
    max_retries: int = 3
    base_backoff_s: float = 1.0
    # B3: multi-box / distributed cluster sharding
    shard_index: int = 0  # node shard index (0 <= shard_index < shard_total)
    shard_total: int = 1  # total number of cluster nodes / shards
    # idempotent incremental run
    manifest_path: str = "work/manifest.json"
    # batching of model-boundary calls
    ocr_warm: bool = True  # preload OCR engine once before the pool
    embed_batch_size: int = (
        32  # row-batch for embedding calls (seam); fp16 envelope on the
    )
    # 4 GB RTX 3050 (B≈32 @ L=1024 is near-OOM) — the chunk pipeline
    # passes its own token-budget caps and never inherits this

    def snapshot(self) -> dict:
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}
