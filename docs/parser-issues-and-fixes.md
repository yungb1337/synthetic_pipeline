# Parser Issues & Fixes — Verified Backlog

> Source: full code-level review + multi-pass verification of `app/parser` and `app/processing`.
> Every issue below was verified against actual source code, not documentation. Measured
> values come from the repo's own benchmark artifact
> (`checkpoints/run/run-2026-09-08-memory-hardening/reports/benchmark-b100.md`:
> 0.46 pages/s, 2.19 s/page, ~2.6 GB peak RSS on the docling band). All projected
> "After" values are **estimates**, clearly labeled.
>
> Behavioral baseline at time of writing: `pytest tests/` → **236 passed, 2 skipped,
> 0 failed** in 119.7s.
>
> Note: this is the *current* backlog. It supersedes the older audit
> `docs/parser-production-readiness-failure-points.md` (F-01..F-11), whose findings
> (atomic writes, logging, BrokenExecutor recovery, resume merge, blank-page gate,
> ledger journal) have since been implemented.

---

## Summary Table

| ID | Sev | Component | Issue (one line) | Fix effort |
|----|-----|-----------|------------------|------------|
| I-01 | **P0** | native engine | One process-global lock serializes ALL native PDF extraction | Medium |
| I-02 | **P0** | scheduler | Heavy pool can never scale above 1 (dead F-probe publish) | Medium |
| I-03 | **P0** | assembler | Retries run Docling in-process & concurrently (OOM hazard) | Low |
| I-04 | P1 | enrichment engine | Fresh `NativePdfEngine` per page → O(N²) native passes | Low |
| I-05 | P1 | docling loader | Per-run Docling config overrides silently dropped | Medium |
| I-06 | P1 | extraction | Duplicate hashing + double/triple detection per extract | Low |
| I-07 | P1 | page store | `put_page` read-before-write + duplicate prior reads | Low |
| I-08 | P1 | ledger | Duplicate-content race: `write_plan` unlinks journal | Low |
| I-09 | P2 | scheduler | Unbounded per-document result accumulation (no backpressure) | Medium |
| I-10 | P2 | ocr | OCR engine thread-safety unverified under wide pool | Low (verify) |
| I-11 | P2 | scheduler | `_heavy_pool._max_workers` private-attr mutation (fragile) | Low |
| I-12 | P2 | scheduler | `derive_heavy_concurrency` assumes 16 GB when psutil missing | Trivial |
| I-13 | P2 | observability | No percentiles / pool utilization / RSS metrics | Medium |
| I-14 | P3 | ocr / cleanups | `batch_ocr_bytes` seam unwired; minor cleanups | Trivial |

---

# P0 — Critical (must fix before scale)

## I-01 — Native band serialized by one process-global lock

**Severity:** P0 · **Files:** `app/parser/engines/native_pdf.py`, `app/parser/scheduler.py`

### Problem (verified)

`NativePdfEngine` uses a single `self._lock = threading.RLock()` held for **both**
`_open_and_compute_median()` and every `extract_page()` call.

The scheduler builds **one shared engine instance per process**
(`Scheduler.__init__` → `self._native_engine = NativePdfEngine(config)`,
returned by `_get_engine()` for the NATIVE band). Consequence: the lock is
**process-global across every document**, so the entire native band — the wide
`native_pool` with `min(32, 2×CPU)` threads — executes **one page at a time**.
The architecture docs claim a "wide native pool that scales by hardware"; the
runtime does not.

**Worse (verified refinement):** the document-wide **median font-size scan**
(an O(N_pages) full-text pass over every page) runs **under the same lock**.
The first page of each new document blocks every other document's pages for
the entire scan. For a 300-page PDF, one document's median computation
freezes the whole native band.

**Related hazard (verified):** the 16-entry doc-handle cache (`_doc_cache`)
**evicts and closes** the oldest `fitz.Document` while pages of that same
document may still be queued in the pool → potential mid-extraction close
(surfaces as a contained FAILED page, but it is a correctness hazard the fix
must address together).

### Fix

1. **Per-document locking**: replace the single RLock with a lock keyed by
   `src_path` (dict of locks + a meta-lock). Each document's pages serialize
   against each other (required: one `fitz.Document` handle is not
   thread-safe across pages) but different documents extract fully in parallel.
2. **Compute the median outside the page lock**: do the one-time median scan
   under the *document's* lock only (it already caches), never under a
   global lock.
3. **Close-safety**: `close()` and cache eviction must acquire the target
   document's lock before closing its handle, so an in-flight page can never
   race a close.
4. Optionally make eviction in-flight-aware (don't evict a document that
   has queued pages).

### Regression risk

- **Heading classification parity**: the document-wide median drives heading
  labels (the F1 fix). Per-document locking preserves the same median — no
  output change. A per-page median (not proposed) would drift.
- **fitz thread-safety**: one `fitz.Document` is NOT safe for concurrent
  page access — keep document-level serialization. Do not remove it.

### Validation

- New concurrency test: parse 3+ distinct PDFs concurrently on a multi-core
  box; assert wall time < serialized time (and < ~50% of it).
- Golden-output diff: block/table/image counts and reading order identical
  to the current parser for the standard fixture set.
- Existing suite green: `test_page_centric.py`, `test_extraction_quality.py`.

### Before vs After — example (estimated)

3 PDFs × 20 pages each, 8-core box, native band, T = per-page extraction time.

| Metric | Before (verified behavior) | After (estimate) |
|---|---|---|
| Execution model | 1 page at a time process-wide | 1 page at a time per document |
| Wall time for the batch | ~60 × T (fully serialized) | ~20 × T + ε (~3× faster) |
| CPU utilization | ~1 core | ~3–8 cores |
| p95 page latency | Inflated by cross-doc lock queue | Bounded by own document only |
| Median-scan blocking | First page of each doc blocks ALL docs | Blocks only its own document |

---

## I-02 — Heavy pool can never scale above 1 (dead F-probe)

**Severity:** P0 · **Files:** `app/parser/scheduler.py`, `app/parser/config.py`, checkpoint docs

### Problem (verified)

- `Scheduler.__init__` (auto path) derives heavy concurrency via
  `derive_heavy_concurrency(F=None)` → **always returns 1**.
- The design (comments in `scheduler.py`, module docstring, and
  `checkpoints/run/run-2026-08-19-page-centric/engineer-report.md` which marks
  C1 "**FIXED**") claims the per-engine RAM footprint `F` is measured **inside
  the heavy worker** and published to the orchestrator via the shared
  `multiprocessing.Value` `_heavy_f_value` (created in `_get_heavy_pool`).
- **Repo-wide search confirms no code ever writes to `_heavy_f_value` or
  `_mp_f_value`.** `_heavy_initializer` only sets env vars and warms the
  engine; `ResourceGovernor.measure_footprint()` exists but is never called.
  The publish half of the feature was never implemented.
- Consequence: `governor.measured_f` stays `None` forever, `periodic_recheck`
  never adjusts anything, and the heavy pool runs at concurrency 1 unless
  `--heavy-concurrency` is passed explicitly. The documented "scale by
  hardware" auto-scaling for the Docling band is **documentation, not code** —
  and the checkpoint report claiming it works is wrong.

### Fix (choose one)

- **Option A (implement the design):** after the first successful
  `convert_path` in a heavy worker, measure the `psutil` RSS delta and write
  it to `_heavy_f_value`. The orchestrator's `run_plan` recheck loop (already
  written) then reads it and re-derives heavy concurrency. Keep the existing
  downward-only live semantic; apply upward changes to future runs only.
- **Option B (simpler, honest — recommended first):** delete the dead probe
  scaffolding (`_mp_f_value`, `_heavy_f_value`, `measure_footprint`),
  document that heavy concurrency is operator-set (`--heavy-concurrency`),
  and default it to a RAM-derived heuristic computed once at Scheduler init
  (total RAM ÷ conservative per-worker budget, e.g. 2.5 GB) — no mid-flight
  changes. Correct the checkpoint report.

### Regression risk

- Upward scaling without RAM safety caused the original `std::bad_alloc`
  cascade this design was built to prevent. Any auto-scaling must keep
  `HEADROOM = 0.80` and the 2 GB base-overhead budget, and prefer
  downward-only live adjustment.
- Option B changes no runtime behavior (still 1 by default); only docs/tests.

### Validation

- Extend `test_page_centric.py` governor tests for the chosen option.
- Option A: integration test that after a docling page completes,
  `governor.measured_f` is set and a subsequent derive returns >1 on a
  large-RAM fixture.

### Before vs After — example (estimated)

100-page docling-routed PDF on a 32 GB box, per-page convert ≈ 2.19 s
(measured baseline for this band).

| Metric | Before (verified) | After Option A (est.) | After Option B (explicit flag) |
|---|---|---|---|
| Heavy workers used | 1 | ~9 ((32×0.8−2)/2.6) | operator-set (e.g. 8) |
| Wall time | ~219 s | ~25–30 s | ~30 s |
| Throughput | 0.46 pages/s | ~3–4 pages/s | ~3.5 pages/s |
| Peak RSS | ~2.6 GB | ~2.6 GB × workers (~23 GB) | same, operator-managed |
| Auto-scales down under RAM pressure | No (probe dead) | Yes (existing recheck loop) | No (static) |

---

## I-03 — Assembler retries run Docling in-process & concurrently

**Severity:** P0 · **Files:** `app/parser/assembler.py` (`_retry_pages`)

### Problem (verified)

`Assembler._retry_pages` re-runs failed/missing pages **synchronously in the
calling thread**, constructing engines directly (`EnrichmentEngine(...)`,
`HeavyDoclingEngine(...)`, ...). For docling-band pages this executes a full
Docling conversion **in the batch worker's thread — in-process**.

Under batch concurrency (`BatchWorker` runs `min(16, CPU+1)` document
threads), **multiple threads can run concurrent in-process Docling
conversions**. That is exactly the hazard the scheduler's
`_get_in_process_heavy_pool` exists to prevent — its own comment:
*"Docling never runs concurrent page conversions in the same process (which
causes massive RAM spikes, ONNX race conditions, and std::bad_alloc)."*

This is a **reliability hazard under batch load**, not just a throughput
issue: retries cluster after a transient failure (e.g. engine blip), which
is precisely when the system is least able to absorb extra RAM pressure.

### Fix

Route retries through the scheduler's existing pools instead of calling
engines directly:

```python
# assembler: build PageWorkItems for retry pages and delegate
results = self.scheduler.run_plan_for_pages(plan, retry_pages)
```

- Add a small method on `Scheduler` (e.g. `run_plan_for_pages`, or reuse
  `run_plan` with a filtered plan) that submits only the retry items to the
  normal pools — docling goes to the heavy pool / in-process pool, native
  band to the native pool.
- Keep the existing B2 skip (`engine_unavailable` pages are never retried)
  and the "restore prior OK page" logic untouched.
- The `Extractor` already holds the scheduler — wire it into the
  `Assembler` constructor (today it takes `ledger` only).

### Regression risk

- Retry latency now depends on pool availability (a busy heavy pool delays
  retries). Acceptable: bounded, and correctness-preserving.
- Must preserve: attempt accounting (G3), dead-letter semantics, the B2
  engine-unavailable skip, and non-docling band behavior.

### Validation

- Regression test: force a docling page failure in a 4-thread batch; assert
  (via a monkeypatched conversion counter) that no two conversions overlap
  in one process.
- Existing assembler tests green; dead-letter path unchanged.

### Before vs After — example (estimated)

16-thread batch; a transient engine blip fails docling pages in 5 documents;
each triggers a retry of 3 docling pages.

| Metric | Before (verified behavior) | After (estimate) |
|---|---|---|
| Docling conversions in-process | Up to 5 threads × 3 pages, concurrent | 0 (all pool-contained) |
| Peak RSS during retry window | Spikes toward multi-GB (OOM/bad_alloc risk) | Bounded by heavy pool (1 worker) |
| `std::bad_alloc` exposure | Real hazard (the exact historical failure) | Eliminated |
| Retry latency | Fast but dangerous | Slightly slower, safe |

---

# P1 — High impact

## I-04 — Enrichment is O(N²): fresh `NativePdfEngine` per page

**Severity:** P1 · **Files:** `app/parser/engines/enrichment.py`

### Problem (verified)

`EnrichmentEngine.process()` constructs `NativePdfEngine(self.config)` **fresh
for every page**. The shared `Scheduler._enrichment_engine` hoist doesn't
help because the inner engine is built inside `process()`. Each fresh engine
re-opens the PDF and re-scans the **whole document** for the median font size
(the F1 fix caches per engine instance — but the instance is thrown away
every page).

Result: for an N-page enrichment document, the median pass alone runs
N × (full-document text scan) = **O(N²)** page-extractions of work.

### Fix

Give `EnrichmentEngine` a single cached `NativePdfEngine` instance:

```python
def __init__(self, config):
    self.config = config
    self._native = NativePdfEngine(config)   # created once, reused per page
def process(self, item):
    res = self._native.process(item)
    ...
```

Since `Scheduler` hoists one `EnrichmentEngine` per process
(`self._enrichment_engine`), the inner native engine (with its per-document
median cache) is then reused across pages of the same document — O(N) total.
Note this makes I-01's per-document lock a prerequisite for safety: the
shared inner engine must use per-document locking (it will, after I-01).

### Regression risk

- Low. Heading parity is *improved* (same cached document-wide median for
  every page of a document, which is exactly the F1 intent).
- Must ensure `close()` propagates to the inner engine (fitz handles).

### Validation

- Unit test: two `process()` calls for pages of the same document reuse the
  median (spy on fitz.open count) — before: 2 opens; after: 1.
- Fixture diff: block counts identical for enrichment-band fixtures.

### Before vs After — example (estimated)

30-page scanned-ish PDF on the enrichment band, per-page native extraction
t ≈ 15 ms, full-doc median scan ≈ 30 × t = 450 ms.

| Metric | Before (verified) | After (estimate) |
|---|---|---|
| fitz document opens | 30 (one per page) | 1 |
| Median scans | 30 × full document | 1 × full document |
| Native-stage work | O(N²) ≈ 13.5 s | O(N) ≈ 0.9 s |
| OCR stage | unchanged | unchanged |

---

## I-05 — Docling config overrides silently dropped

**Severity:** P1 · **Files:** `app/parser/loaders/docling_loader.py` (`convert_path`, `_build_converter`)

### Problem (verified)

- `convert_path(path, page, models_dir, table_mode, ocr)` accepts
  `table_mode` and `ocr` (forwarded from `PageWorkItem`) and **ignores
  them** — its own inline comment admits it ("this is still using the
  global engine").
- `_build_converter()` reads `default_config()` (a fresh `ParserConfig()`)
  for `docling_ocr`, `docling_table_mode`,
  `docling_generate_picture_images` — **not the caller's `ParserConfig`**.
- Net effect: any per-run override of `docling_table_mode` (e.g. the
  documented "ACCURATE opt-in"), `docling_ocr`, or
  `docling_generate_picture_images` **never reaches the converter**. The
  "F-11 fix" is incomplete; config provenance lies about what ran.

### Fix

Key the converter cache by the options that affect construction:

```python
_engine_cache: dict[tuple, object] = {}
# (ocr, table_mode, generate_picture_images) -> converter

def get_engine(ocr=None, table_mode="", generate_picture_images=True):
    key = (bool(ocr), (table_mode or "").upper(), bool(generate_picture_images))
    ...  # build once per key, reuse
```

- `convert_path` passes the item's `table_mode`/`ocr` through;
  `HeavyDoclingEngine.process` forwards all three fields from the
  `PageWorkItem` (add `docling_generate_picture_images` to `PageWorkItem`).
- Document that each distinct key costs one engine warm-up per worker
  process (bounded: keys are few).
- Alternative (cheaper): declare these construction-time options
  Scheduler-level (one config per process) and make per-item overrides a
  validation error — removes silent divergence either way.

### Regression risk

- TableFormer mode changes table output for corpora relying on the
  (accidentally always-FAST) behavior — gate the change behind the config
  actually being set, and run the table fixture suite (D2 tests).
- More engines per process = more RAM on the heavy band; keep keys minimal.

### Validation

- Unit test: `ParserConfig(docling_table_mode="ACCURATE")` produces a
  converter whose `table_structure_options.mode == ACCURATE` (introspect).
- Fixture regression: tables 1/5/6 remain correct under default FAST.

### Before vs After — example

Corpus run configured with `docling_table_mode="ACCURATE"` for a
borderless-table-heavy fixture.

| Metric | Before (verified) | After (estimate) |
|---|---|---|
| Mode the converter actually used | FAST (silent) | ACCURATE |
| Config snapshot (provenance) | Claims ACCURATE — false | Matches reality |
| Dense-table row fidelity | FAST behavior | ACCURATE behavior (as documented) |
| Silent divergence | Yes | No |

---

## I-06 — Duplicate hashing & repeated detection

**Severity:** P1 · **Files:** `app/parser/extraction.py`, `app/parser/source.py`

### Problem (verified)

Per `extract()` call with the batch executor (which already supplies
`sha256`):

1. `SourceScan.scan` **always** re-hashes the full file
   (`hashlib.sha256(data).hexdigest()`) even when the hash was handed in.
2. `detection.detect(data, filename)` runs **twice**: once in
   `Extractor.extract` and again inside `SourceScan.scan`.
3. On the resume fast-path, detect runs a **third** time
   (the resume branch calls `detection.detect` again).

Detection is cheap for magic-prefix hits but does real work otherwise
(full zip listing for container files, head decode + JSON parse for text
sniffs). For a 50 MB file, the extra full-buffer SHA-256 is ~100–200 ms of
wasted CPU per document.

### Fix

- Thread the known hash through: `SourceScan.scan(data, filename, store,
  sha256=None)`; pass the already-computed `detected` object into `scan`
  instead of re-detecting.
- Resume fast-path: reuse the `detected` object instead of re-calling
  `detect` (it only needs slug/mime for the report).
- Keep `detection.detect` semantics identical — only call-count changes.

### Regression risk

- Very low. The only subtle piece: `doc_id` must remain `d-{sha[:16]}` —
  the hash handed in is already the source of truth in the batch path
  (executor passes `ref.sha256`). Verify single-doc (`sha256=None`) path
  unchanged.

### Validation

- Unit test: monkeypatch counters on hashing/`detect`; assert 1 hash,
  1 detect per extract.
- Idempotency test: same bytes via both paths produce the same `doc_id`.

### Before vs After — example (estimated)

50 MB PDF in the batch executor (hash already known from corpus scan).

| Metric | Before (verified) | After (estimate) |
|---|---|---|
| Full-file SHA-256 computations | 2 | 1 |
| `detection.detect` calls | 2 (3 on resume) | 1 |
| Wasted CPU per document | ~100–200 ms (hash) + detect work | ~0 |
| 10k-doc corpus savings | — | ~20–30 min of CPU (estimate) |

---

## I-07 — `PageStore.put_page` read-before-write (+ duplicate prior reads)

**Severity:** P1 · **Files:** `app/parser/storage_pages.py`, `app/parser/scheduler.py`

### Problem (verified)

- `PageStore.put_page` calls `self.get_page(...)` (a full disk read +
  Pydantic parse of the page JSON) on **every** page write, just to check
  whether a prior OK page exists (the B1 append-never-destroy rule).
- `Scheduler._collect` **also** reads the prior page (`page_store.get_page`)
  on every FAILED result before calling `put_page` — so a failed page is
  read from disk twice, and every successful page incurs one needless
  read+parse.

### Fix

- Add a cheap existence/status probe to `PageStore`:
  `page_status(doc_id, idx) -> str | None` that reads only the tiny
  `status` field (or maintain a per-doc in-memory status map inside
  `PageStore`, populated on `put_page`/init-scan).
- `_collect` uses the probe instead of `get_page`; `put_page` keeps a
  full-object read only when it actually needs to *restore* a prior result.

### Regression risk

- Low. The B1 guarantee (never clobber a durable OK page) must be
  preserved — keep the exact same decision logic, just cheaper lookups.
- In-memory map must handle multi-process safety: per-doc lock or
  trust-the-disk on conflict (current behavior already re-reads on demand).

### Validation

- Unit test: `put_page` on a fresh page performs no full JSON parse
  (counter on `PageResult.from_json`).
- Ledger/page-store round-trip tests green (B1 semantics preserved).

### Before vs After — example (estimated)

100-page document, page-JSON parse ≈ 1–3 ms each.

| Metric | Before (verified) | After (estimate) |
|---|---|---|
| Disk reads + JSON parses during persist | 100 (read-before-write) | 0 (status probe only) |
| Extra reads on FAILED pages | 2× each | 1× only when restoring |
| Persist-stage time | +0.1–0.3 s per document | ~0 |

---

## I-08 — Duplicate-content race: journal wipe in `Ledger.write_plan`

**Severity:** P1 (correctness under concurrency) · **Files:** `app/parser/storage_pages.py`

### Problem (verified)

Documents with identical bytes share a `doc_id` (`d-{sha[:16]}`), so two
threads parsing the same content concurrently share the ledger at
`manifest/<doc_id>/plan.json` + `journal.jsonl`.

`Ledger.write_plan` **unlinks `journal.jsonl`** after writing the plan
(consolidation). Interleaving:

1. Thread A writes plan (journal consolidated, file unlinked).
2. Thread B appends page updates to the journal (recreated).
3. Thread A (a second planning round) writes plan again → **unlinks the
   journal**, discarding B's page updates.

Result: attempts/statuses can regress (a page recorded OK by B can appear
pending again in the plan A wrote). Page artifacts themselves are safe
(atomic per-file writes); only ledger bookkeeping can regress. Low
probability, real under duplicate-content corpora with retries.

### Fix

- Make journal consolidation **append-safe**: instead of unlinking, write a
  per-plan-generation marker or consolidate only entries older than the
  plan's write timestamp; simplest robust option: a per-process
  `threading.Lock` in `Ledger` + rename the journal to
  `journal.jsonl.<gen>` and fold *that* file during consolidation.
- Longer term: deduplicate at the corpus layer (the manifest already
  prevents re-processing within a run) and/or take a per-doc advisory lock
  (filelock) in `extract` for duplicate content.

### Regression risk

- Low. Journal replay logic (`load_plan`) must handle the renamed/
  generational files. Keep `LedgerCorruptionError` path intact.

### Validation

- Concurrency unit test: two threads plan+update the same doc_id
  simultaneously; assert no page status regresses after both finish.

### Before vs After — example

Two identical PDFs submitted simultaneously (same sha → same doc_id).

| Metric | Before (verified behavior) | After (estimate) |
|---|---|---|
| Journal entries lost | Possible (B's updates wiped by A's write) | None (generational journal) |
| Ledger attempts/status | Can regress to earlier snapshot | Monotonic |
| Page artifacts | Safe (atomic writes) | Safe (unchanged) |

---

# P2 — Medium impact

## I-09 — Unbounded per-document result accumulation

**Severity:** P2 · **Files:** `app/parser/scheduler.py` (`run_plan`), `app/parser/assembler.py`

### Problem (verified)

`run_plan` submits **all** page futures at once and accumulates every
completed `PageResult` (including image blobs — `RecoveredImage.blob` holds
full PNG bytes) in the `results` list until assembly. For a 500-page
image-heavy PDF this can hold hundreds of MB in RAM per in-flight document
— multiplied by `BatchWorker.concurrency` (up to 16) document threads.

The per-page persistence model already stores each result durably; holding
all results in memory is redundant with the page store.

### Fix

- Stream assembly: as each future completes, persist (already done) and
  **drop the blob** from the in-memory copy (`res.images[i].blob = b""`
  after `put_page`), reloading blobs from the page store only at fold time
  for pages that made it into the final document — or have `_fold_results`
  re-read `PageResult`s from the page store instead of the in-memory list.
- Optionally bound in-flight futures (submit in windows of
  `2 × native_concurrency`) for true backpressure; the fixed pools already
  bound *execution*, not *submission*.

### Regression risk

- Medium: blob lifecycle touches image provenance (`put_image` uses blobs
  in the assembler). Must guarantee blobs are available at `Assembler`
  fold/`put_image` time — reload from page store is the safe pattern.
  Test image-heavy fixtures end-to-end.

### Validation

- Memory test: parse a large image PDF; assert RSS no longer scales with
  page count × blob size.
- Image fixture suite: storage_ref/checksums identical before/after.

### Before vs After — example (estimated)

500-page PDF, ~200 KB average image blob, 300 images, 8 batch threads.

| Metric | Before (verified behavior) | After (estimate) |
|---|---|---|
| In-memory blob retention | ~60 MB × 8 docs ≈ 0.5 GB held until assemble | Blobs dropped after persist |
| Peak RSS contribution | Scales with pages × blobs | ~constant per document |
| Batch OOM margin on 16 GB box | Tight under concurrency | Comfortable |

---

## I-10 — OCR engine thread-safety unverified

**Severity:** P2 (risk, not proven defect) · **Files:** `app/parser/ocr.py`, `app/parser/engines/enrichment.py`

### Problem (verified as a risk)

`ocr._engine` is a module-global `RapidOCR` instance shared by all
native-pool threads; enrichment OCR (`ocr_bytes`) runs concurrently on the
wide pool. RapidOCR's thread-safety is undocumented. Concurrent ONNX
session use is *usually* safe (ORT sessions are thread-safe for `run`),
but the rapidocr wrapper may mutate internal state (preprocessing buffers).
Flagged, not proven — needs an explicit decision: guard or verify.

### Fix

- Cheapest safe option: add a `threading.Lock` around `_engine(image)`
  calls in `ocr_image` (OCR is compute-bound; serialization cost is
  acceptable and matches how the enrichment band is already used).
- Or: verify thread-safety with a stress test (4 threads × 50 images,
  assert no crash and per-thread results correct); if clean, document it.

### Regression risk

- The lock adds contention (throughput loss on OCR-heavy corpora) —
  measure before committing; the stress-test route avoids that cost.

### Validation

- Stress test as above; corpus OCR outputs byte-identical.

### Before vs After — example (estimated)

Enrichment band, 4 threads OCR-ing scanned pages concurrently.

| Metric | Before | After (lock) | After (verified-safe) |
|---|---|---|---|
| Concurrent OCR calls | Unbounded (risk unknown) | 1 (serialized) | 4 (unchanged) |
| Crash/teardown risk | Undocumented | Eliminated | Documented-OK |
| OCR throughput | ? | ~1× (serialized) | ~4× |

---

## I-11 — Private attribute mutation of the live pool

**Severity:** P2 · **Files:** `app/parser/scheduler.py` (`run_plan` recheck block)

### Problem (verified)

The downward-shrink path sets `self._heavy_pool._max_workers = new_c` — a
private CPython attribute of `ProcessPoolExecutor`. It works today but is
an implementation detail that may change across Python versions (this repo
runs 3.14). The shrink also doesn't affect in-flight work (documented as
accepted in `project_memory/questions.md`).

### Fix

- Replace with a supported mechanism: track desired concurrency and apply
  it at pool (re)build time (the pool is already rebuilt after
  `BrokenExecutor`); or wrap the pool behind a small `HeavyPool` façade
  that owns the resize semantics in one place.

### Regression risk

- Minimal; behavior preserved (downward-only, future submissions).

### Validation

- Unit test: shrink request during a run → next (re)built pool uses the
  new size.

### Before vs After — example

RAM pressure detected mid-run on Python 3.14 (current) vs a future 3.15
where `_max_workers` is renamed/removed.

| Metric | Before | After |
|---|---|---|
| Mechanism | Private attr poke (may break silently) | Supported façade/apply-at-rebuild |
| Breakage mode | Silent (attr exists, ignored or errors) | Explicit, tested |
| Downward shrink | Works today | Works on any CPython |

---

## I-12 — Unsafe 16 GB default when psutil is missing

**Severity:** P2 (trivial fix) · **Files:** `app/parser/scheduler.py` (`derive_heavy_concurrency`)

### Problem (verified)

When `psutil` is not installed, `derive_heavy_concurrency` assumes
`16 * 1024**3` total RAM and (with an F) can derive concurrency >1 on a
smaller box → memory pressure exactly where the design promised safety.
(The F=None path correctly floors to 1; this only matters once I-02 makes
F available.)

### Fix

On missing psutil, return 1 (same safe floor as the F=None path). One line
+ test.

### Before vs After — example

8 GB box without psutil, F measured = 2.6 GB.

| Metric | Before | After |
|---|---|---|
| Assumed RAM | 16 GB (fabricated) | Unknown → safe floor |
| Derived heavy concurrency | 3 ((16×0.8−2)/2.6) | 1 |
| OOM exposure | Real | None |

---

## I-13 — Observability gap

**Severity:** P2 · **Files:** `app/parser/scheduler.py`, `app/parser/events.py`, `app/processing/executor.py`

### Problem (verified)

The system emits document/page events (events.jsonl) and per-stage timings
(`detect_ms`, `route_ms`, `scan_ms`, `plan_ms`, `run_ms`, `assemble_ms` in
`doc_report`), but there are **no aggregates**: no p50/p95/p99 page latency,
no pool utilization (native/heavy queue depth), no RSS tracking, no
retry/dead-letter rates surfaced. The benchmark report was produced by a
one-off script, not the runtime. Every future optimization on this backlog
currently must be validated with hand-rolled scripts.

### Fix

- Emit a periodic (or per-run-end) `parser.metrics.v1` event containing:
  page-latency percentiles (from `PageResult.timings`), pool queue depths
  sampled per N completions, process RSS (`psutil`, already a dependency),
  retry counts, dead-letter counts, band mix (native/enrichment/docling).
- Add to `BatchReport`: pages/s, band mix, retry rate, dead-letter rate.
- Keep it additive — no schema changes to existing events.

### Regression risk

- None (additive events/fields only).

### Validation

- Run the fixture corpus; assert the metrics event appears with expected
  keys; benchmark script consumes it instead of its own instrumentation.

### Before vs After — example

A nightly 10k-document batch run.

| Metric | Before (verified) | After |
|---|---|---|
| p95 page latency | Not available anywhere | In metrics event |
| Heavy pool utilization | Invisible (and currently pinned at 1, I-02) | Sampled per run |
| Dead-letter/retry rates | Only in scattered logs | In BatchReport |
| Post-change validation | Hand-rolled scripts | Standard metrics diff |

---

# P3 — Optional

## I-14 — Unwired batch-OCR seam & minor cleanups

**Severity:** P3 · **Files:** `app/parser/ocr.py`, misc

### Problem (verified)

- `ocr.batch_ocr_bytes` exists but is unused ("not yet wired into the
  pipeline (scale-batch spec overstates this); kept as the seam").
- Minor: `EnrichmentEngine` re-renders via a fresh `fitz.open` per OCR'd
  page (acceptable at one render per scanned page; could reuse the document
  handle after I-01).

### Fix

- Either wire `batch_ocr_bytes` into a real batching caller or delete it
  (dead seams rot).
- Post-I-01: let `EnrichmentEngine` reuse the native engine's open document
  handle for its OCR render (one open per document, not per page).

### Before vs After — example

Enrichment document with 10 scanned pages.

| Metric | Before | After |
|---|---|---|
| fitz opens for OCR renders | 10 | 1 (reused handle) |
| Dead code surface | `batch_ocr_bytes` unused | Wired or removed |

---

## Recommended execution order

1. **I-01** — per-document native locking (unlocks the whole native band;
   prerequisite for I-04).
2. **I-03** — contain assembler retries in the pools (reliability).
3. **I-02** — fix the heavy-scaling story (implement Option B first).
4. **I-04** — enrichment engine reuse.
5. **I-05** — honor docling overrides (provenance truth).
6. **I-06 / I-07 / I-08** — cheap duplicate-work and race fixes.
7. **I-13** — observability so every later change is measured.
8. **I-09 → I-10 → I-11 → I-12 → I-14** — as capacity allows.

Each fix lands with its listed validation steps and the full suite
(`236 passed` baseline) green.

## Overall score (unchanged post-verification): 6.5/10

Production-grade reliability machinery (zero-silent-loss gate, per-page
persistence, dead-lettering, atomic writes, pool-rebuild recovery) — but the
throughput defaults contradict the documented architecture: the native band
is locked serial (I-01), heavy auto-scaling is dead code (I-02), and the
retry path can re-create the exact OOM hazard the design exists to prevent
(I-03).
