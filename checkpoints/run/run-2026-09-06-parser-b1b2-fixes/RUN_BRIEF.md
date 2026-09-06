# Run brief — run-2026-09-06-parser-b1b2-fixes

**Trigger:** the mass `engine_unavailable` cascade (run-2026-09-04) all-but-destroyed the
numeric store (25/26 ledgers dead, 1 DOM surviving). Root cause traced to a
resource-exhaustion cascade + three production bugs (B1/B2/B3 — fixed here).

## Root cause (Fact)
1. Boulder: two parse processes + two downloaders + a 2.7GB orphan parse were run
   CONCURRENTLY on a 15.4GB box, leaving ~1.7GB available. paging-file exhaustion
   (WinError 1455) followed.
2. Docling model build then failed inside worker processes. `_build_converter()`
   caches `_engine = False`, `convert_path()` returns None, and
   `HeavyDoclingEngine` returns `FAILED / engine_unavailable` for EVERY page.
3. The planner's `engine_available()` fallback is PER-PROCESS: it succeeded in the
   orchestrator (kept `band=docling`) while worker builds failed.
4. Re-parse then CLOBBERED durable OK pages with FAILED records (B1) and the
   assembler retried the same cached-False engine pointlessly (B2), misreporting
   good docs as `dead` (B3).

## Fixes applied (production level, no per-doc special casing)
- **B1 (durability / "append, never destroy"):**
  - `app/parser/storage_pages.py::PageStore.put_page` — a FAILED/DEAD record is
    never written over an existing durable OK page.
  - `app/parser/scheduler.py::Scheduler._collect` — a FAILED/DEAD result with a
    durable prior OK page restores the prior OK result (artifact + ledger survive).
- **B2 (futile retry):** `app/parser/assembler.py::Assembler._retry_pages` — pages
  that failed with `category=engine_unavailable` are skipped during in-process
  retry (the engine is cached False; retrying burns wall-time). They dead-letter,
  and a later clean run retries them.
- **B3 (misleading downgrade):** covered by B1 (ledger stays OK) + B2 (no pointless
  re-failure). A poisoned re-parse no longer downgrades a good doc to dead.

## Utils
- `scripts/run_parser_benchmark.py` gained `--heavy-concurrency N` so the heavy
  (Docling) pool can be bounded on RAM-limited boxes instead of being RAM-derived
  from TOTAL memory (which over-derives workers on this 16GB laptop).

## This run
- Input: 36-PDF snapshot `sources/pdf_snapshot_b01/`
- Output store: this run dir `parsed/`, reports to `reports/`
- Heavy concurrency pinned: 1 (single docling worker — memory-safe for this box)
- Goal: demonstrate a CLEAN parse of the snapshot (the poisoned store stays as
  evidence in run-2026-09-04), then judge accuracy, then benchmark.