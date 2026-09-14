# Fix Tracker — Parser Issues Backlog (I-01 → I-14)

Tracks the one-by-one implementation of every issue from
`docs/parser-issues-and-fixes.md`, highest severity first.
Baseline before any fix: **236 passed, 2 skipped** (pytest, full suite).
Final state after all fixes: **286 passed, 2 skipped** (+50 tests, 0 failures
at every checkpoint).

---

## Final verification summary

- Every fix landed with its own regression tests (50 new tests total across
  8 new test files).
- Full-suite checkpoints: after P0 → **251 passed**, after P1 → **270
  passed**, after P2/P3 → **286 passed, 2 skipped** (the 2 skips are
  pre-existing environment skips: a fallback-path test skipped because
  Docling IS installed, and a fixture PDF not present on this machine).
- Two transient defects introduced and caught **by the tests during this
  effort** were fixed immediately: an `Extractor.__init__` init-order bug
  (I-03) and a misplaced `assembler.page_store` assignment (I-09) — both are
  noted in the per-issue entries below.

| ID | Sev | Status | Files touched | Tests added | Suite after fix |
|----|-----|--------|---------------|-------------|-----------------|
| I-01 | P0 | ✅ done | app/parser/engines/native_pdf.py | tests/test_i01_native_concurrency.py (4 tests) | 38 passed, 1 skipped |
| I-02 | P0 | ✅ done | app/parser/scheduler.py | tests/test_i02_heavy_concurrency.py (7 tests) | 251 passed, 2 skipped |
| I-03 | P0 | ✅ done | app/parser/assembler.py, app/parser/scheduler.py, app/parser/extraction.py | tests/test_i03_retry_containment.py (4 tests) | 251 passed, 2 skipped |
| I-04 | P1 | ✅ done | app/parser/engines/enrichment.py, app/parser/scheduler.py | tests/test_i04_enrichment_reuse.py (3 tests) | 32 passed, 1 skipped (subset) |
| I-05 | P1 | ✅ done | app/parser/loaders/docling_loader.py | tests/test_i05_docling_overrides.py (4 tests) | 32 passed, 1 skipped (subset) |
| I-06 | P1 | ✅ done | app/parser/source.py, app/parser/extraction.py | tests/test_i06_dedupe_hash_detect.py (5 tests) | 270 passed, 2 skipped |
| I-07 | P1 | ✅ done | app/parser/storage_pages.py, app/parser/scheduler.py | tests/test_i07_pagestore_probe.py (4 tests) | 270 passed, 2 skipped |
| I-08 | P1 | ✅ done | app/parser/storage_pages.py | tests/test_i08_ledger_race.py (3 tests) | 270 passed, 2 skipped |
| I-09 | P2 | ✅ done | app/parser/scheduler.py, app/parser/assembler.py, app/parser/extraction.py | tests/test_i09_blob_backpressure.py (4 tests) | 286 passed, 2 skipped |
| I-10 | P2 | ✅ done | app/parser/ocr.py | tests/test_i10_ocr_call_lock.py (3 tests) | 286 passed, 2 skipped |
| I-11 | P2 | ✅ done | (resolved via I-02 refactor of scheduler.py) | tests/test_i11_i12_pool_and_psutil.py (2 I-11 tests) | 286 passed, 2 skipped |
| I-12 | P2 | ✅ done | app/parser/scheduler.py (both derive paths) | tests/test_i11_i12_pool_and_psutil.py (2 I-12 tests) | 286 passed, 2 skipped |
| I-13 | P2 | ✅ done | app/parser/scheduler.py, app/processing/executor.py | tests/test_i13_metrics.py (4 tests) | 286 passed, 2 skipped |
| I-14 | P3 | ✅ done | app/parser/ocr.py (dead seam removed) | (grep-verified: zero callers) | 286 passed, 2 skipped |

---

## Execution log

### I-01 (P0) — Native band serialized by one process-global lock
- **Status:** ✅ DONE
- **Change:** replaced the single process-global `self._lock` RLock with
  per-document locks (`_lock_for(src_path)`, dict + meta-lock). Pages of the
  SAME document still serialize (fitz handle safety); DIFFERENT documents
  extract in parallel. The O(N) document-wide median scan now runs under its
  own document's lock only. Handle-cache eviction is in-flight-aware
  (`_evict_if_over_budget_locked` never closes a handle whose document lock
  is busy; cache cap raised 16 → 32). `close()` closes each handle under its
  own document lock.
- **Tests:** `tests/test_i01_native_concurrency.py` — cross-document overlap
  probe (fails on the old global lock), same-document non-overlap,
  sequential-vs-concurrent output parity, close/reopen cycle.
- **Suite:** 38 passed, 1 skipped (test_i01_native_concurrency.py +
  test_page_centric.py + test_extraction_quality.py + test_routing_enrichment.py).

### I-02 (P0) — Heavy pool can never scale above 1 (dead F-probe)
- **Status:** ✅ DONE (Option B — honest, once-computed default)
- **Change:** removed the dead F-probe scaffolding (`_heavy_f_value`,
  `_mp_f_value`, `measure_footprint`, `_f_probe_done`) — repo-verified dead
  code (nothing ever wrote the shared Value). Auto default is now
  `ResourceGovernor.derive_default_heavy_concurrency()`: usable RAM
  (total × 0.80 headroom, cgroup-capped) minus 2 GiB base overhead, divided
  by a 2.5 GiB per-worker budget, floored at 1 — computed ONCE at Scheduler
  init. Without psutil it returns the safe floor 1 (never fabricates RAM,
  closing I-12 on this path). Explicit `--heavy-concurrency` still wins.
  `periodic_recheck` retained, acts only with explicitly-set `measured_f`,
  downward-only. Removed the mid-run recheck loop + private `_max_workers`
  mutation from `run_plan` (I-11 addressed here at the source).
- **Tests:** 32 GB box derives >1 (old code pinned 1 — regression detector);
  4/8/16 GB exact math; psutil-missing → 1; override wins; scaffolding gone;
  recheck still downward-only.
- **Suite:** 251 passed, 2 skipped (full run after P0 checkpoint).

### I-03 (P0) — Assembler retries run Docling in-process & concurrently
- **Status:** ✅ DONE
- **Change:** `Assembler` gains optional `scheduler` param; when wired,
  `_retry_pages` dispatches retries via the new
  `Scheduler.run_plan_for_pages(plan, page_indexes)` (filtered shallow copy
  of the plan → normal pools: docling → heavy/in-process pool, native band →
  native pool). B2 `engine_unavailable` skip now filters BEFORE dispatch so
  it applies to both paths. `Extractor.__init__` wires its scheduler into
  the assembler (scheduler resolution moved before assembler construction).
  Legacy in-process path retained for no-scheduler tests. Fixed en route:
  `Extractor.__init__` had referenced `self.scheduler` before assignment
  (caught by the safety-net test).
- **Tests:** dispatch-through-pool (recording fake scheduler), B2 skip
  before dispatch, ownership wiring (source introspection), legacy fallback.
- **Suite:** 251 passed, 2 skipped (full run after P0 checkpoint).

### I-04 (P1) — Enrichment O(N²)
- **Status:** ✅ DONE
- **Change:** `EnrichmentEngine.__init__` now creates ONE inner
  `NativePdfEngine` (reused for every page) instead of a fresh engine per
  `process()` — the inner engine's per-document caches (open fitz handle,
  document-wide median) persist across pages: O(N) total native work, not
  O(N²). Added `EnrichmentEngine.close()` and `Scheduler.close()` now
  propagates to it (fitz-handle hygiene). Safe under concurrency because of
  the I-01 per-document locking.
- **Tests:** inner-engine identity across process() calls; 4-page document →
  exactly 1 fitz.open (was 4) + 1 median entry; scheduler close propagation.
- **Suite:** 32 passed, 1 skipped (test_i04 + test_routing_enrichment +
  test_page_centric).

### I-05 (P1) — Docling config overrides silently dropped
- **Status:** ✅ DONE
- **Change:** `_build_converter(ocr, table_mode, generate_picture_images)`
  now takes explicit construction inputs (explicit args WIN; shipped
  defaults only when omitted). `get_engine()` caches engines per
  `(ocr, table_mode, generate_picture_images)` key (`_engine_cache`), with
  the key resolved exactly as the build resolves it (empty mode → shipped
  default) to avoid duplicate identical converters. `convert_path` forwards
  the per-item `table_mode`/`ocr` into `get_engine` — the accepted-and-
  ignored behavior is gone; provenance now matches reality.
- **Tests:** default path builds once + reuses; distinct overrides → distinct
  engines with per-key caching (case-insensitive); convert_path forwards
  overrides; unavailable build → None.
- **Suite:** 32 passed, 1 skipped (test_i05 + test_docling_loader +
  test_memory_hardening_config).

### I-06 (P1) — Duplicate hashing & repeated detection
- **Status:** ✅ DONE
- **Change:** `Extractor.extract` detects ONCE up front (resume fast-path
  reuses the result instead of a third detect call) and passes both
  `detected` and `source_hash=sha` into `SourceScan.scan`, which now accepts
  optional `detected` / `source_hash` params (standalone callers keep the
  old behavior). Batch path: 1 hash + 1 detect per extract (was 2 hashes,
  2–3 detects). `doc_id` derivation unchanged.
- **Tests:** scan with supplied detected/hash performs neither (counted via
  monkeypatch); standalone scan unchanged; extract → 1 detect; resume
  fast-path → 1 detect; doc_id stable from supplied hash.
- **Suite:** 270 passed, 2 skipped (full run after P1 checkpoint).

### I-07 (P1) — PageStore put_page read-before-write
- **Status:** ✅ DONE
- **Change:** new `PageStore.page_status(doc_id, idx)` — regex probe of the
  single top-level `status` field in the persisted JSON (no full parse, no
  Pydantic part construction; disk stays source of truth so it is
  cross-process safe). `put_page` uses the probe for the B1 check (decision
  byte-equivalent: corrupt → None → overwrite, as before); the scheduler's
  restore path probes first and pays the full read ONLY when a durable OK
  page actually exists. 100-page doc: 100 full parses during persist → 0.
- **Tests:** probe reads without building PageResult; B1 rule unchanged;
  corrupt prior probes None + overwrite allowed; restore path probes before
  full read (0 reads without prior OK, exactly 1 with).
- **Suite:** 270 passed, 2 skipped (full run after P1 checkpoint).

### I-08 (P1) — Duplicate-content race: journal wipe
- **Status:** ✅ DONE
- **Change:** `Ledger` now has `_jl` (journal lock); `update_page` appends
  under it and `write_plan` consolidates (fold journal → plan) + unlinks
  under the SAME lock, so a concurrent appender's records either land in the
  consolidated plan or remain in the fresh journal for the next replay —
  never wiped unseen. Journal folding extracted into shared
  `_fold_page_record` (one semantics for replay + consolidation). Documented
  cosmetic tradeoff: a concurrent replan can double-count `attempts` by one;
  status/checksum/errors are replace-safe.
- **Tests:** pending-journal record survives a concurrent write_plan;
  3-appenders × 2-rewriters storm → all records visible, no status
  regression; replay semantics unchanged via the shared helper.
- **Suite:** 270 passed, 2 skipped (full run after P1 checkpoint).

### I-09 (P2) — Unbounded per-document result accumulation
- **Status:** ✅ DONE
- **Change:** `Scheduler._collect` now releases in-RAM image blob bytes
  (`img.blob = b""`) AFTER the page (with base64 blobs) is durably persisted
  in the page store. `Assembler` gains `_reload_page_blobs` + `page_store`
  attribute (wired in `Extractor.__init__`): before fold, image-bearing pages
  with stripped blobs are replaced by their durable counterparts, so
  `put_image` sees identical bytes. Faithful degradation when no durable copy
  exists (no invented bytes, no crash). 500-page image-heavy PDF: blob
  retention now ~constant per document instead of pages × blobs.
- **Tests:** strip-after-persist (durable copy keeps bytes, in-RAM is empty);
  reload restores blobs; no-op when nothing stripped; missing durable copy →
  faithful empty blobs.
- **Suite:** 286 passed, 2 skipped (full run after P2/P3 checkpoint).
- **Fix-loop note:** `Extractor.__init__` set `assembler.page_store` before
  constructing the assembler (init-order bug — caught by the safety-net
  test) and the Assembler lacked a `page_store = None` default; both fixed.

### I-10 (P2) — OCR engine thread-safety
- **Status:** ✅ DONE (lock route chosen — cheapest safe option)
- **Change:** new `_call_lock` in `app/parser/ocr.py` serializes engine
  CALLS (`_engine(image)` inside `ocr_image`), distinct from the init
  `_lock`. Correctness no longer depends on RapidOCR's undocumented
  thread-safety; OCR calls are compute-bound, so serialization cost is
  acceptable (and measurable via I-13 metrics if revisited).
- **Tests:** fake engine detects re-entrancy — 8 calls across 4 threads →
  max_concurrent == 1, all results correct; call lock ≠ init lock;
  unavailable engine → [].
- **Suite:** 286 passed, 2 skipped (full run after P2/P3 checkpoint).

### I-11 (P2) — Private attribute mutation of live pool
- **Status:** ✅ DONE (resolved at the source by the I-02 refactor)
- **Change:** the mid-run recheck loop that poked
  `self._heavy_pool._max_workers` (a private CPython attribute) was removed
  together with the dead probe in I-02. The pool is built once from the
  declared `heavy_concurrency` and rebuilt (after BrokenExecutor) from the
  current value; `periodic_recheck` adjusts only the declared value.
- **Tests:** source-scan asserts `_max_workers` appears nowhere in
  scheduler.py; pool constructor receives the declared max_workers;
  recheck still downward-only.
- **Suite:** 286 passed, 2 skipped (full run after P2/P3 checkpoint).

### I-12 (P2) — Unsafe 16 GB default when psutil missing
- **Status:** ✅ DONE
- **Change:** both derive paths now return the safe floor 1 when psutil is
  unavailable: `derive_heavy_concurrency` (which previously fabricated a
  16 GiB box) and `derive_default_heavy_concurrency` (already safe since
  I-02). RAM unknown ⇒ concurrency 1, never a guessed size.
- **Tests:** both paths → 1 with psutil mapped to None in sys.modules (even
  with a measured F present).
- **Suite:** 286 passed, 2 skipped (full run after P2/P3 checkpoint).

### I-13 (P2) — Observability gap
- **Status:** ✅ DONE
- **Change:** (1) `Scheduler` gains an optional `metrics_sink`; at the end of
  every `run_plan` it emits an additive `parser.metrics.v1` event with
  pages_total, band mix, status mix, per-page turnaround p50/p95/p99
  (linear-interpolated `_percentile`), run wall-time, process RSS (psutil,
  optional), and the declared native/heavy concurrency. (2)
  `processing.executor` bridges scheduler metrics into `BatchReport` via a
  lock-guarded live-report reference (`merge_metrics`), and adds
  `pages_seen`, `docs_per_s`, `pages_per_s`, `by_band`, `by_page_status`,
  turnaround percentiles and `rss_mb`. Additive only — no existing event
  schema changed; a missing sink or psutil never crashes the run.
- **Tests:** end-to-end run_plan → one metrics event with exact band/status
  mixes and ordered percentiles; no-sink path emits nothing and never
  raises; percentile math (incl. single-element and interpolation cases);
  BatchReport merge semantics across two payloads + throughput math.
- **Suite:** 286 passed, 2 skipped (full run after P2/P3 checkpoint).

### I-14 (P3) — Unwired batch-OCR seam
- **Status:** ✅ DONE (removed)
- **Change:** deleted `ocr.batch_ocr_bytes` — grep-verified zero callers in
  app/, scripts/ and tests/ (dead seams rot; re-add a wired batch caller if
  a real batching need appears). The enrichment OCR-render fitz-open reuse
  suggested in the backlog is deferred: it's covered structurally by the
  I-04 inner-engine reuse (cached document handle) and is not needed for
  correctness.
- **Suite:** 286 passed, 2 skipped (full run after P2/P3 checkpoint).
