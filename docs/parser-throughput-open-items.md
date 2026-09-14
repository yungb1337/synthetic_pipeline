# Parser Throughput — Open Items Tracker

**Created:** 2026-09-14 · **Corpus:** 100 PMC PDFs / 1,297 pages · **Box:** 16 CPU, 15.4 GB RAM
**Current baseline:** 0.76–0.80 pages/s (postfix A/B), 0 failures/dead/unparsed.
**Target:** >2x throughput (~1.6+ pages/s) with **zero functional change** (DOM, yields, determinism, hard gates preserved).

## Binding constraint
Throughput is RAM-bound on the Docling band (93% of pages). Each heavy worker holds ~2.6 GB; `ResourceGovernor` caps the pool at `max(1, floor((usable - 2 GiB) / 2.5 GiB))` = 4 workers on this box. The ceiling is 4 concurrent Docling conversions regardless of idle CPUs.

## Status legend
- [ ] OPEN — not started
- [~] IN PROGRESS
- [x] CLOSED — implemented, tests passing

---

## GROUP A — Pure improvements (no architecture change)

### A1 — [x] Fix O(N²) Docling item scan
- **File:** `app/parser/engines/heavy_docling.py:89-106`
- **Problem:** `doc.iterate_items()` was called once per page and filtered `page_no != target`. For an N-page doc that was N x N item iterations — same bug class as I-04 (fixed in enrichment band), previously open here.
- **Fix:** Build `by_page: dict[int, list]` once, index per page.
- **Expected gain:** 5–15% on docling band. **Status:** CLOSED (tested & verified).

### A2 — [x] Cache source bytes per document in heavy engine
- **File:** `app/parser/engines/heavy_docling.py:34-47, 114`
- **Problem:** `open(item.src_path, "rb").read()` ran on every page even though bytes are already inside the `ConversionResult`.
- **Fix:** Read once per document path and cache in `_src_cache` under a thread lock.
- **Expected gain:** 2–5%. **Status:** CLOSED (tested & verified).

### A3 — [x] Tighten + RSS-trigger heavy worker recycling
- **File:** `app/parser/config.py:61-71`, `app/parser/scheduler.py:481-515`
- **Problem:** Recycling previously only fired after 20 pages; worker RSS climbed monotonically (2,407 -> 3,469 MB across a run).
- **Fix:** Tightened default `heavy_pool_max_tasks_per_child` from 20 to 10; added `_maybe_rebuild_pool` hook triggered when orchestrator RSS exceeds `heavy_pool_rss_threshold_mb`.
- **Expected gain:** Sustains peak throughput on long runs. **Status:** CLOSED (tested & verified).

### A4 — [x] Cap batch concurrency for docling-heavy corpora
- **File:** `app/processing/config.py:12-21`, `app/processing/executor.py:213-234`
- **Problem:** Batch thread pool had fixed concurrency of 17 while heavy pool was RAM-capped at 4, causing thread contention on docling pages.
- **Fix:** Added `concurrency_cap_heavy` (default 2), capping effective batch concurrency at `heavy_concurrency * 2`.
- **Expected gain:** Smoother throughput, lower peak RSS. **Status:** CLOSED (tested & verified).

### A5 — [x] Measure F for the RAM governor
- **File:** `app/parser/scheduler.py:183-186, 252-269`
- **Problem:** Per-worker budget was hardcoded to 2.5 GiB.
- **Fix:** Added `ResourceGovernor.calibrate_f()` helper to measure real RSS footprint and feed into `derive_default_heavy_concurrency`.
- **Expected gain:** +1 worker when RAM allows. **Status:** CLOSED (tested & verified).

### A6 — [x] Batch page-store writes
- **File:** `app/parser/storage_pages.py:254-273`
- **Problem:** Page ledger updates written individually on every page.
- **Fix:** Added `Ledger.update_pages_batch` for atomic batch writes to journal.
- **Expected gain:** Eliminates I/O tail on long runs. **Status:** CLOSED (tested & verified).

---

## GROUP B — Architectural changes (deferred)

### B1 — [x] Shard processes on the box (Architecture B)
- **File:** `app/parser/cli.py:30-48`, `scripts/parse_folder.py:68-135`
- **Problem:** Single-process Python orchestrator is bottlenecked by GIL, serialization locks, and a single process pool.
- **Fix:** Added `--shard-index` and `--shard-total` to `app.parser.cli`, plus `--shards N` in `scripts/parse_folder.py` to launch parallel shard processes to the same store.
- **Expected gain:** ~2.5-3.4x (2.0-2.7 pages/s). **Status:** CLOSED (implemented & verified).

### B2 — [x] Split Docling into its own service (Architecture E)
- **File:** `app/parser/engines/remote_docling.py`, `app/parser/docling_service.py`, `app/parser/config.py:42`, `app/parser/scheduler.py:91-105, 395-410`
- **Problem:** Heavy Docling process footprint (~2.6 GB/worker) starves native processing on the same box.
- **Fix:** Added standalone Docling HTTP microservice (`app.parser.docling_service`) and `RemoteDoclingEngine` in `Scheduler` to offload heavy page conversions over HTTP/RPC with non-blocking thread scheduling.
- **Expected gain:** 3-5x local speedup (offloading heavy band) or arbitrary horizontal scale. **Status:** CLOSED (implemented & verified).

### B3 — [x] Multi-box sharding (Architecture C)
- **File:** `app/processing/corpus.py:83-100`, `app/processing/config.py:10-15`, `app/processing/executor.py:204-207`, `app/processing/cli.py:34-52`, `scripts/run_cluster_shards.py`
- **Problem:** Single box has finite physical RAM/CPU limits; scaling beyond 1 box requires distributed coordination.
- **Fix:** Added deterministic SHA256 corpus partitioning (`shard_doc_refs`), `--shard-index` and `--shard-total` in batch executor and CLI, with non-colliding atomic writes to shared store.
- **Expected gain:** ~3.8-4.0x linear scaling across 4 nodes (~6.0-8.0 pages/s). **Status:** CLOSED (implemented & verified).

### B4 — [-] GPU-accelerated Docling (Architecture F) [SKIPPED]
- **Reason for skipping:** Gated on dedicated GPU instances with >=16 GB VRAM and golden-output LLM judge parity verification. Consumer 4 GB GPU incurs `std::bad_alloc` due to shared host RAM pressure.

### B5 — [-] Broker queue + stateless workers (Architecture D) [SKIPPED]
- **Reason for skipping:** Architectural pattern for 10^5 - 10^7 docs/day enterprise elasticity with Celery/SQS/RabbitMQ; premature for current single-box and cluster file-sharded workloads.

---

## Notes
- All GROUP A gains are **Inference + Recommendation**, not measurement. Re-run the 100-doc corpus after each fix; the I-13 `parser.metrics.v1` hook (`BatchReport.merge_metrics`) is already wired for cheap like-for-like measurement.
- Box-condition drift is real: baseline started with 5.55 GB free vs 3.87 GB for Run A.
