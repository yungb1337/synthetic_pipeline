ARCHITECTURE: APPROVED

# Architecture — Page-centric parser execution model (MedFactory AI Synthetic Data Factory)

**Run:** `run-2026-08-19-page-centric` · **Gate:** CHIEF ARCHITECT · **Date:** 2026-08-19
**Verdict target:** `ARCHITECTURE: APPROVED`
**Note:** This spec is appended as ADR-013 to `project_memory/architecture_decisions.md` on exit. No application code is written here — spec + ADR only.

---

## 0. TL;DR for the implementer (Gate 3 / Gate 4)

Redesign `app/parser` so that **a page is the unit of work and the unit of durable
storage**, while **a document remains the unit of orchestration**. The router (ADR-011)
still decides one band per document; that band is applied page-by-page. Heavy Docling
work runs in a **bounded `ProcessPoolExecutor`** whose size is **derived from measured
RAM/GPU**, never a fixed cap. Every Docling job is `convert(page_range=(p,p))` so peak
C++ heap is bounded to one page. Every page is written to a **page store** + recorded in
a **per-document ledger**; assembly is allowed to succeed **only** when
`assembled_page_set == expected_page_set` (established *before* paging). `Extractor.extract`
keeps its public signature and `FilesystemStore` (`raw/`,`dom/`,`images/`) keeps its layout.

---

## 1. Context & problem statement

**Root cause (Research, established Fact).** Docling's C++ layout/segmentation engine
allocates a per-instance heap that scales with the number of pages in flight. Running
multiple **document-level** Docling workers concurrently caused 20 `std::bad_alloc`
crashes in the C++ preprocess stage on the 15-doc corpus. Docling then
**swallows** these per-page exceptions and **back-fills empty `PageItem` stubs** to
preserve numbering (`StandardPdfPipeline._add_failed_pages_to_document`), so
`len(doc.pages)` equals the expected count even when pages failed. The current loader
(`app/parser/loaders/docling_loader.py::_convert`) reads only `result.document` and
**never inspects `result.status` or `result.input.page_count`**, so a 24→10 page
truncation is reported as success — the silent data loss the brief mandates we eliminate.

**The fix (Research, established Fact + Recommendation).** `page_range=(p,p)` is a
*supported, documented knob* that bounds how many pages Docling segments at once. With `page_range=(p,p)` only
one page is in the native parser at a time, collapsing peak heap to ~one page. Pair that
with a **persistent, warmed Docling engine per heavy worker process** (NOT a fresh
process per page), `OMP_NUM_THREADS=1`/`MKL_NUM_THREADS=1` in each heavy process to neutralize the BLAS-thread
multiplier, and `release_native_memory_every_n_pages=1`. Scale concurrency by **measured
hardware**, not by a fixed cap.

**Objective (this run).** Page = fundamental processing AND durable-storage unit; document
= orchestration unit. Resource-aware concurrency decoupled into `native_pool` (wide
`ThreadPoolExecutor`) and `heavy_pool` (bounded `ProcessPoolExecutor`). Zero silent page
loss via explicit per-page status + `expected_page_set` validation. Idempotent resume via
page store + ledger. Backward-compatible with `Extractor.extract()` and `FilesystemStore`.
No distributed system; filesystem only; no external DB.

---

## 2. Target architecture diagram (ASCII)

```
                          ┌──────────────────────────────────────────────┐
   source bytes + filename│                                                │
        ─────────────────▶│  SourceScan (app/parser/source.py)            │
                          │   detect + establish EXPECTED_PAGE_SET         │
                          │   (fitz len(pdf) for PDF; 1 for other formats) │
                          │   write ONE reusable source path:             │
                          │   <store>/manifest/<doc_id>/src.<ext>         │
                          └───────────────┬────────────────────────────────┘
                                          │ SourceManifest(expected_page_set, page_count,
                                          │              detected, slug, metadata)
                                          ▼
                          ┌──────────────────────────────────────────────┐
                          │  Planner (app/parser/planner.py)              │
                          │   - route band (ADR-011 decision or override) │
                          │   - build ExecutionPlan + N PageWorkItems     │
                          │   - write ledger plan.json (status=PENDING)   │
                          │   - RESUME: skip pages already OK in store    │
                          └───────────────┬────────────────────────────────┘
                                          │ ExecutionPlan
                                          ▼
            ┌─────────────────────────────────────────────────────────────┐
            │  Scheduler (app/parser/scheduler.py)                         │
            │   ResourceGovernor  ── derives heavy_concurrency = f(ram,F)  │
            │   ┌──────────────┐             ┌──────────────────────────┐  │
            │   │ native_pool  │             │ heavy_pool               │  │
            │   │ ThreadPool   │             │ ProcessPoolExecutor      │  │
            │   │ (wide)       │             │ max_workers=heavy_conc.  │  │
            │   │ native_pdf   │             │ each worker lazily       │  │
            │   │ enrichment   │             │ builds ONE warmed        │  │
            │   │ image        │             │ Docling engine + sets    │  │
            │   │ simple       │             │ OMP/MKL_NUM_THREADS=1    │  │
            │   └──────┬───────┘             └────────────┬─────────────┘  │
            │          │ page jobs        page jobs ▲     │                 │
            └──────────┼──────────────────│──────────────┼─────────────────┘
                       ▼                  │              ▼
            ┌─────────────────────┐       │   ┌──────────────────────────┐
            │ Page engines        │       │   │ heavy_docling engine      │
            │ (engines/*.py)      │       │   │ convert(src, page_range=  │
            │ each -> PageResult   │       │   │   (p,p)); check status +  │
            │ (status + page parts)│       │   │ content; map to Recovered │
            └──────────┬──────────┘       │   │ slice; return PageResult   │
                       │                  │   └────────────┬─────────────┘
                       ▼                  │                │
            ┌──────────────────────────────────────────────────────────┐
            │  PageStore + Ledger (app/parser/storage_pages.py)         │
            │   put_page -> pages/<doc_id>/p<idx>/page-v<ver>.docJSON   │
            │   ledger  -> manifest/<doc_id>/plan.json (per-page status)│
            └───────────────┬──────────────────────────────────────────┘
                            │ PageResult[] (assembled from store or mem)
                            ▼
            ┌──────────────────────────────────────────────────────────┐
            │  Assembler + DocumentValidator (app/parser/assembler.py)  │
            │   - fold page slices -> ONE RecoveredDocument (page order) │
            │   - DocumentValidator: assembled_set == expected_set?      │
            │   - if NOT: re-enqueue missing/partial pages (retry+backoff)│
            │   - after max_retries: DEAD-letter page, doc status=FAILED │
            │     with explicit actual-vs-expected report (NEVER silent) │
            │   - on success: DocumentBuilder.build(rec) -> Document     │
            └───────────────┬──────────────────────────────────────────┘
                            │ Document (unchanged schema)
                            ▼
            ┌──────────────────────────────────────────────────────────┐
            │  Store (app/parser/storage.py FilesystemStore)            │
            │   put_dom  -> dom/<doc_id>/dom-v<ver>.docJSON  (legacy OK) │
            │   put_raw  -> raw/<sha>.<ext>                             │
            │   put_image-> images/<doc_id>/<sha>.<ext>                 │
            │   emit document.parsed.v1 (route, actual/expected pages)  │
            └──────────────────────────────────────────────────────────┘
```

Single-doc path: `Extractor.extract` is a thin synchronous facade =
`detect -> route -> Planner.plan -> Scheduler.run_plan (blocks) -> Assembler.assemble ->
Store.put_*`. Batch path (`app/processing/executor.py`): one shared `Scheduler` per
process drives `run_plan` per document; the top-level `ThreadPoolExecutor` (doc-level)
stays for cross-document parallelism.

---

## 3. Module-by-module spec (responsibilities, key types, SEAMS)

All new modules live under `app/parser/`. Existing contracts (`Extractor.extract`,
`FilesystemStore`, `Document`/`Page`/`Block` schemas, `RoutingDecision`, router) are
**unchanged** unless explicitly additive.

### 3.1 `app/parser/source.py` — SourceScan
- `SourceManifest`: `doc_id:str`, `source_hash:str` (sha256[:16] -> `d-...`),
  `expected_page_set:list[int]`, `page_count:int`, `slug:str`, `mime:str`,
  `declared_extension:str`, `probe:str`, `page_sizes:dict[int,(w,h)]`,
  `src_path:str` (the reusable source file), `metadata:dict` (title/author/etc. where
  cheaply available; full metadata still folded at assembly for PDFs via fitz).
- `SourceScan.scan(data:bytes, filename:str) -> SourceManifest`:
  - detect (reuse `app.parser.detection.detect`).
  - For **PDF**: open with `fitz.open(stream=data)` cheaply to get `page_count`
    (`len(pdf)`) and `page_sizes`, then **close**. `expected_page_set =
    list(range(page_count))`. This is the single source of truth established *before*
    any Docling call.
  - For **other formats**: `expected_page_set = [0]`, `page_count = 1`.
  - Write the bytes **once** to a reusable path `<store>/manifest/<doc_id>/src.<ext>`
    (this is the single temp file for the whole document). Return
    `src_path` pointing there.

### 3.2 `app/parser/engines/base.py` — PageEngine protocol
- `class PageEngine(Protocol):`
  - `route_band: str` (one of `native|enrichment|docling|image|simple`; an engine
    advertises which band it serves).
  - `def process(self, item: PageWorkItem) -> PageResult: ...`
- `PageResult` and `PageStatus` live in `app/parser/page_result.py` (see §4).
- **Seam:** engines take a `PageWorkItem` (carries `src_path`, not the raw bytes) and
  return a `PageResult`. No engine touches the DOM, the ledger, or the Store — pure
  transform. This keeps them unit-testable and keeps the loader→builder contract
  (`Recovered*` parts) intact.

### 3.3 `app/parser/engines/native_pdf.py` — NativePdfEngine (`route_band="native"`)
- Refactor the existing `Loaders._pdf` single-pass logic into a **per-page** extractor:
  open `fitz.open(item.src_path)`, extract one page `p` -> `RecoveredBlock`s (text +
  font/bold), `RecoveredTable`s (`find_tables`), `RecoveredImage`s. Returns a `PageResult`
  with `status=OK` if ≥1 content part, else `OK` with empty parts (a genuinely blank
  page is still a successfully-processed page, status OK, no error). Releases the GIL
  during C calls -> safe in `native_pool`.

### 3.4 `app/parser/engines/enrichment.py` — EnrichmentEngine (`route_band="enrichment"`)
- Per-page variant of ADR-012: run `NativePdfEngine.process` for the page; if the page
  produced **zero text blocks**, render that ONE page with `fitz` and OCR it via the
  existing `ocr.ocr_bytes` (RapidOCR singleton). Returns a `PageResult` carrying the
  OCR `RecoveredBlock`s with `source="ocr"`. One render per scanned page (matches
  ADR-012's "exactly one Pixmap per empty page"). `config.ocr_enabled` gates it.

### 3.5 `app/parser/engines/heavy_docling.py` — HeavyDoclingEngine (`route_band="docling"`)
- Lives **inside a heavy worker process** (spawned by `heavy_pool`). On first use it
  lazily builds the singleton Docling `DocumentConverter` via a new
  `docling_loader.get_engine()` (the existing `_build_converter`/singleton logic, moved
  so it is the **per-process** engine, not per-page).
- `process(item)`:
  - Calls `docling_loader.convert_path(item.src_path, page=item.page_index,
    models_dir=item.models_dir)` (NEW entrypoint — see §3.9). Inside, that calls
    `converter.convert(src_path, page_range=(item.page_index+1, item.page_index+1))`
    (Docling pages are 1-based; our `page_index` is 0-based).
  - **Status check (the silent-loss fix):** read `ConversionResult.status` and
    `result.errors`; assert the returned `DoclingDocument` has page
    `item.page_index+1` with ≥1 block/table/cell. If `status in {FAILURE}` or the page
    is empty/stub -> `PageResult.status = PARTIAL|FAILED` with `errors` listing
    `(page_no, category, message)`. **Never** synthesize a "complete" result from an
    empty stub.
  - Map the single page's items -> `RecoveredBlock/Table/Image` (reuse the existing
    `_map_item`/`_map_table`/`_map_image`/`_recover_formula_text` helpers, scoped to the
    one page). `reading_order_authoritative=True`, `docling_version`, `layout_model`
    carried forward.
  - Env already pinned in child process: `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`,
    `TORCHDYNAMO_DISABLE=1`; pipeline option `release_native_memory_every_n_pages=1`,
    `images_scale` modest.

### 3.6 `app/parser/engines/image.py` — ImageEngine (`route_band="image"`)
- Standalone image OCR (the existing `Loaders._image` path): `ocr.ocr_bytes` over the
  source bytes for page 0. Returns `PageResult(page_index=0, status=OK)`. One page per
  image file.

### 3.7 `app/parser/engines/simple.py` — SimpleEngine (`route_band="simple"`)
- All inherently single-page formats: plaintext, csv, tsv, json, xml, html, markdown,
  docx, xlsx. Reuses the existing `Loaders._text/_delimited/_json/_xml/_html/_markdown/
  _docx/_xlsx` helpers (now returning only page-0 parts). Returns `PageResult(page_index=0)`.

### 3.8 `app/parser/page_result.py` — PageResult / PageStatus
- `PageStatus` enum: `PENDING | OK | PARTIAL | FAILED | DEAD`. (PENDING only in ledger;
  engines emit OK/PARTIAL/FAILED; DEAD set by Assembler after exhausted retries.)
- `@dataclass PageResult`:
  - `doc_id:str`, `page_index:int`, `route:str`
  - `status:PageStatus`
  - `blocks:list[RecoveredBlock]`, `tables:list[RecoveredTable]`,
    `images:list[RecoveredImage]`, `annotations:list[RecoveredAnnotation]`
  - `content_present:bool` (True iff ≥1 block/table/cell recovered — the per-page
    content check result)
  - `errors:list[dict]` (each `{"page_no":int|None,"category":str,"message":str}`)
  - `engine_version:str|None`, `docling_version:str|None`
  - `source_hash:str`, `checksum:str` (content-address of the serialized page parts, for
    idempotent dedup / resume)
  - `timings:dict`
  - `to_recovered_slice()` helper -> the per-page `Recovered*` lists (used by Assembler).

### 3.9 `app/parser/loaders/docling_loader.py` — additive change
- Add `convert_path(path:str, page:int, models_dir:str|None=None) -> object|None`
  (and `get_engine()` exposing the per-process singleton). `convert_path` calls
  `converter.convert(path, page_range=(page,page))` **directly on the provided path**
  (NO temp-file write — this is the §7 churn mitigation) and returns `ConversionResult`
  (not just `.document`) so the engine can inspect `status`/`errors`. The existing
  `parse()`/`_convert()` (temp-file-based, whole-doc) may be kept for the non-page
  path but is **not** used by the new engine. Defensive try/except preserved.

### 3.10 `app/parser/planner.py` — Planner
- `ExecutionPlan`: `doc_id`, `source_hash`, `sha` (full), `route:str`,
  `decision:RoutingDecision|None`, `detected_type,mime,declared_extension,probe`,
  `expected_page_set:list[int]` (FACT: set here, before paging), `page_count:int`,
  `page_sizes:dict`, `metadata:dict`, `config_snapshot:dict`,
  `work_items:list[PageWorkItem]`.
- `PageWorkItem`: `doc_id`, `source_hash`, `src_path:str`, `page_index:int`, `route:str`,
  `decision:RoutingDecision|None` (or just `route`), `models_dir:str`, `ocr_enabled:bool`,
  `attempt:int=0`.
- `Planner.plan(manifest:SourceManifest, route:str, decision, config) -> ExecutionPlan`:
  - Builds one `PageWorkItem` per `expected_page_set` element, each tagged with the
    document's band.
  - **Resume:** loads existing `manifest/<doc_id>/plan.json`; any page already `OK` (and
    present in PageStore) is **excluded** from `work_items` (idempotent — never reparse
    done pages).
  - Writes the ledger (`plan.json`) with all pages `PENDING` (or `OK` if resumed).

### 3.11 `app/parser/storage_pages.py` — PageStore + Ledger
- `PageStore`:
  - `put_page(doc_id, page_index, result:PageResult) -> str`: writes
    `pages/<doc_id>/p<page_index>/page-v<ver>.docJSON` (versioned, deterministic
    overwrite of same version; prior versions retained — mirrors `FilesystemStore.put_dom`
    ADR-008 semantics). `ver` = page-schema version (e.g. `v0.1.0`).
  - `get_page(doc_id, page_index) -> PageResult|None`.
  - `page_exists(doc_id, page_index) -> bool` (for resume).
- `Ledger`:
  - `write_plan(doc_id, plan:ExecutionPlan)` -> `<store>/manifest/<doc_id>/plan.json`.
  - `load_plan(doc_id) -> dict|None`.
  - `update_page(doc_id, page_index, status, checksum, engine, attempt, errors)`.
  - `update_assembly(doc_id, status, assembled_set, report)`.
- **Ledger schema** (`manifest/<doc_id>/plan.json`):
  ```json
  {
    "doc_id": "d-xxxx",
    "source_hash": "sha256...",
    "route": "docling",
    "expected_page_set": [0, 1, 2, ..., n-1],
    "page_count": n,
    "created_at": "ISO8601",
    "config_snapshot": { ... },
    "pages": {
      "0": {"status": "ok", "checksum": "<sha>", "engine": "docling-2.x",
            "attempts": 1, "errors": []},
      "1": {"status": "pending", "attempts": 0, "errors": []}
    },
    "assembly": {"status": "pending", "assembled_page_set": [], "report": null}
  }
  ```
- These are **additive** directories under the existing store root (`pages/`,
  `manifest/`); `raw/`,`dom/`,`images/` are untouched.

### 3.12 `app/parser/scheduler.py` — ResourceGovernor + Scheduler
- `ResourceGovernor`:
  - `measure_footprint() -> float`: derives `F` (measured per-engine RAM). Implemented by
    having the heavy pool workers, in their `initializer`, build the engine and parse one
    small + one large representative page, sampling `psutil.Process().memory_info().rss`
    peak, and publishing the max into a `multiprocessing.Value`/`Array`. Governor reads it
    after pool warm-up (cold-start probe). If Docling unavailable -> `F = None` and
    `heavy_concurrency = 1` (degrade to serial heavy, still safe).
  - `derive_heavy_concurrency() -> int` (formula in §5).
  - `periodic_recheck()`: re-derive from `psutil.virtual_memory().available` (and cgroup
    cap) every N docs / seconds; adjust the live `ProcessPoolExecutor` only downward
    (never spawn mid-flight upward surges; upward growth applies to the next run / next
    pool cycle) to avoid disrupting in-flight jobs.
- `Scheduler` (one shared instance per process; holds the pools for the process lifetime
  so engines warm once — no N× warm-up):
  - `native_pool: ThreadPoolExecutor` — wide (`native_concurrency`, default
    `min(32, cpu_count*2)`; cheap, GIL-releasing).
  - `heavy_pool: ProcessPoolExecutor(max_workers=heavy_concurrency)` — `initializer`
    sets `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `TORCHDYNAMO_DISABLE=1`,
    `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` (dormant on CPU box),
    then warms the Docling engine + measures `F`.
  - `run_plan(plan:ExecutionPlan) -> list[PageResult]`:
    - dispatch each `PageWorkItem` to the correct pool by `route` band
      (native/enrichment/image/simple -> native_pool; docling -> heavy_pool).
    - **Backpressure:** submit bounded batches; as futures complete, persist each
      `PageResult` via `PageStore.put_page` and update the ledger (status OK/PARTIAL/
      FAILED). A page that raises (unhandled exception in worker) -> `FAILED` with the
      traceback in `errors` (contained: only that page fails, not the whole doc).
    - Returns the collected `PageResult`s (also durable in PageStore for resume).
  - `close()` / context manager to shut the pools.

### 3.13 `app/parser/assembler.py` — Assembler + DocumentValidator
- `DocumentValidator`:
  - `assembled_page_set = {r.page_index for r in results if r.status in (OK, PARTIAL) and r.content_present}`.
  - `is_complete(expected, assembled) -> bool`: `set(assembled) == set(expected)`.
- `Assembler.assemble(plan, results, store, config) -> (Document|None, ParseOutcome-ish report)`:
  - Fold all page slices (in page order) into ONE `RecoveredDocument`: append
    `RecoveredBlock/Table/Image/Annotation` lists; re-number `seq` globally per kind as
    needed; carry `page_sizes`, `reading_order_authoritative` (True if any page came from
    docling), `docling_version`, `layout_model`, `routing` (the `decision`), `metadata`.
  - If `is_complete` -> build `Document` via the **existing `DocumentBuilder.build(rec,
    doc_id, sha)`** (unchanged — no schema change). Persist images through
    `store.put_image` (sets `storage_ref`, exactly as today), then `store.put_dom` +
    `store.put_raw`. Mark ledger `assembly.status=ok`.
  - If NOT complete -> compute `missing = set(expected) - assembled`; **re-enqueue** the
    missing/partial pages as new `PageWorkItem`s with `attempt+1` (retry policy:
    `max_retries` from config, exponential backoff). Loop until complete or retries
    exhausted.
  - After exhaustion -> pages still missing get ledger `status=DEAD` +
    `assembly.report["dead_pages"]`; `ParseOutcome.status="failed"` with an **explicit**
    `actual_vs_expected` report (e.g. `{"expected":24,"actual":23,"missing":[11]}`). This
    is the anti-silent-loss guarantee: a loss is *loud*, never a fake success.
- **Seam note:** `DocumentBuilder.build` is reused as-is (preferred over an additive
  `assemble_from_pages`).

### 3.14 Rewiring `app/parser/extraction.py` (thin facade, backward-compatible)
- `Extractor.__init__` now also builds/holds a shared `Scheduler` (and a `Planner`,
  `PageStore`, `Ledger`, `Assembler`). `Extractor.extract(data, filename, sha256)` keeps
  its exact signature + `ParseOutcome` return + report keys.
- New body: `detect -> _compute_route (unchanged) -> SourceScan.scan -> Planner.plan ->
  Scheduler.run_plan (blocks for single doc) -> Assembler.assemble (which does Store.put_*
  + event emit) -> return ParseOutcome`. All existing report fields populated from the
  assembled `Document` + ledger.

### 3.15 Rewiring `app/processing/executor.py` (batch path)
- `ParseNormalizePipeline.process` keeps calling `self.extractor.extract(...)` (the facade
  now internally runs the page pipeline). **Change:** the `BatchWorker` creates **one**
  shared `Scheduler` for the process and injects it into each `Extractor` (so the heavy
  `ProcessPoolExecutor` is process-lifetime, warmed once — satisfies "no N× warm-up").
  The top-level `ThreadPoolExecutor(max_workers=config.concurrency)` remains the
  **doc-level** pool. Retries/backoff/manifest logic unchanged.

### 3.16 `app/routing/router.py` + `app/routing/schema.py`
- **No change.** ADR-011 router remains decision-only. Per-page enrichment fallback is
  expressed by the `enrichment` engine's per-page OCR behavior plus an optional Planner
  rule: if `route=="docling"` but a specific page yields no content after a docling
  attempt, the Assembler's retry may route *that page* through `enrichment` as a fallback
  (recorded in ledger).

---

## 4. Data contracts (exact shapes)

### 4.1 `PageStatus` (enum, `page_result.py`)
`PENDING | OK | PARTIAL | FAILED | DEAD`

### 4.2 `PageResult` (dataclass, `page_result.py`)
Fields listed in §3.8. Serializable to `page-v<ver>.docJSON`. `checksum` = sha256 over
canonical JSON of the page parts (idempotency/dedup).

### 4.3 `ExecutionPlan` / `PageWorkItem`
Shapes in §3.10. `expected_page_set` is a `list[int]` (0-based, contiguous for PDFs; `[0]`
for others).

### 4.4 Ledger schema
In §3.11 (`manifest/<doc_id>/plan.json`). Authoritative per-page status + `assembly`
block with `actual_vs_expected`.

### 4.5 Page store path layout
`<store_root>/pages/<doc_id>/p<page_index>/page-v<ver>.docJSON`
- `ver` = page-schema version (additive; independent of the DOM version).
- Same-version write is a deterministic overwrite; prior versions retained (ADR-008).
- `pages/` is **additive** to the existing `raw/`,`dom/`,`images/` layout.

### 4.6 Mapping `RecoveredDocument` per-page slice -> `PageResult` -> `Document`
- A `PageWorkItem` for page `p` produces a `PageResult` whose `blocks/tables/images`
  all carry `page=p`.
- `Assembler` concatenates page slices in `page_index` order into one `RecoveredDocument`.
- Final `Document` is produced by the **unchanged** `DocumentBuilder.build`, so the
  existing `dom/<doc_id>/dom-v<ver>.docJSON` consumers are unaffected.

---

## 5. Resource governor design

**Measured footprint `F` (research §3, Fact+Recommendation):**
- `F` is **measured, not guessed**. Implementation: heavy worker `initializer` builds the
  engine, parses one small + one large representative page, samples
  `psutil.Process().memory_info().rss` peak, publishes max to shared memory.
- Governor reads `F` after pool warm-up (cold-start probe). If Docling absent -> `F=None`
  -> `heavy_concurrency=1`.

**Formula (research §3):**
```
ram_cap        = min(psutil.virtual_memory().total, cgroup_memory_max or +inf)
usable         = ram_cap * HEADROOM            # HEADROOM ≈ 0.80
base_overhead  = fixed non-heavy RSS (orchestrator + native_pool workers)
F              = measured per-engine RAM footprint
heavy_concurrency = max(1, floor((usable - base_overhead) / F))
# if Docling on GPU: also cap by floor(gpu_free / gpu_per_job)   # DORMANT on CPU box
```

**When measured / re-checked:**
- **Cold-start probe:** at `Scheduler` init (first heavy pool start), before any real work.
- **Periodic re-check:** every N completed documents (e.g. 25) or every T seconds, the
  governor re-derives `heavy_concurrency` from `psutil.virtual_memory().available` and the
  cgroup cap. Adjustments apply **downward immediately**; **upward** growth is applied on
  the next pool cycle / next run. "Scale by hardware, not by limiting".

**Environment variables (set inside every heavy worker process):**
- `OMP_NUM_THREADS=1`
- `MKL_NUM_THREADS=1`
- `TORCHDYNAMO_DISABLE=1`
- `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` (dormant on CPU box)
- Docling pipeline option `doc_batch_concurrency=1` / `page_batch_concurrency=1`

**`release_native_memory_every_n_pages=1`:** set on the Docling pipeline options for the
heavy path so the C++ native heap is reclaimed page-by-page.

**Why this neutralizes the BLAS-thread multiplier:** without `OMP/MKL_NUM_THREADS=1`, each
Docling process spawns `cpu_count` OpenMP/MKL threads, each allocating thread-local buffers
— a silent `heavy_concurrency × cpu_count` memory multiplier. Because `F` is measured
*with these env vars already set*, the formula sizes to the true single-thread-per-process
footprint; throughput scales by **process count**, not thread count.

---

## 6. Silent-loss elimination (exact mechanism)

1. **Establish `expected_page_set` BEFORE paging** (Planner): for PDFs from `fitz len(pdf)`.
2. **Per-page status is explicit** (PageResult): every page job returns `OK | PARTIAL |
   FAILED` with `content_present` and `errors`. A Docling `FAILURE`/empty-stub page can
   never masquerade as success because each page is its own `convert(page_range=(p,p))`
   call and we inspect `ConversionResult.status` + `result.errors` + actual content.
3. **`DocumentValidator` gate** (Assembler): assembly is allowed to succeed **only** when
   `assembled_page_set == set(expected_page_set)`. Mismatch -> identify `missing`,
   **re-enqueue** those pages (retry + backoff, `max_retries` from config).
4. **Dead-letter on exhaustion:** after retries, still-missing pages get ledger
   `status=DEAD` and the document is reported `failed` with an **explicit**
   `actual_vs_expected`. A loss is loud — never a fake "parsed 24 pages" when 23 recovered.
5. **Docling version-drift guard:** a startup guard test asserts `ConversionResult.status`,
   `ErrorItem.page_no`, and `page_range` exist on the installed `docling`. If absent ->
   engine marked unavailable, doc falls back to native (graceful, never silent).

---

## 7. Trade-off review

**Options considered (research §7 matrix + Decision Challenger):**

| Strategy | Peak RAM / worker | N× warm-up? | Resume cost | Throughput | Verdict |
|---|---|---|---|---|---|
| **(b) Document-level** (current `convert()` whole doc) | ~all pages in flight + whole-doc native parser | No (1 load) | full re-parse | high per-doc but **OOM-prone** | **REJECT** — proven root cause (20 bad_alloc) |
| **(c) Chunked ranges** (e.g. 4 pp/call) | ~range size × concurrency | No | re-parse range | medium | Middle; still multiplies peak RAM; partial-loss detection still needed |
| **(a) Page-centric, persistent warmed engine per process** | **1 page** | **No** if engine reused per process | **trivial (page already stored)** | aggregate = `heavy_concurrency` | **ADOPT** |

**Why (a) wins (research evidence):**
- Root cause is document-length × concurrency heap multiplication. `page_range=(p,p)` is
  the *supported* knob that bounds peak C++ heap to one page.
- N× warm-up hazard is the decisive counter-argument: Docling init loads layout+table+OCR
  models (hundreds of MB, seconds). Page-centric **only works if the engine is a persistent
  per-process singleton reused across all page calls** — a fixed `ProcessPoolExecutor` of
  long-lived workers, never a fresh subprocess per page.
- Serialized-heavy + wide-native is strictly better here: the heavy stage is memory-bound
  and GIL/CUDA-limited, so extra heavy concurrency yields near-zero CPU gain while
  multiplying peak RAM; the native lane (PyMuPDF) releases the GIL and scales with cores.

**Cost of N temp-file writes per doc — and mitigation:** `SourceScan` writes the source
**once** to a reusable path `manifest/<doc_id>/src.<ext>`; the heavy engine calls
`docling_loader.convert_path(src_path, page_range=(p,p))` **directly on that path** (no
per-page temp). Net: 1 write + N cheap reads.

**BLAS-thread multiplier risk — and how the governor neutralizes it:** `OMP_NUM_THREADS=1`
+ `MKL_NUM_THREADS=1` set **in every heavy worker process**; `F` measured with them set.

**What would change my mind (challenge):** if a future corpus shows page-at-a-time Docling
is *slower* than a small chunked range AND fits in RAM, a chunked-range hybrid (bounded,
e.g. 2–4 pp) could be revisited — but it would still require the per-page `status`
validation and the same `heavy_pool` governor.

---

## 8. Migration / cutover

**Strategy: Direct cutover.** The redesign is an internal execution-model change; no new
service, no schema break.
- **`Extractor.extract` public API:** unchanged signature `(data, filename, sha256)`,
  unchanged `ParseOutcome` + report keys.
- **`FilesystemStore` layout:** `raw/`,`dom/`,`images/` unchanged. **Additive** new dirs
  `pages/<doc_id>/...` and `manifest/<doc_id>/plan.json` under the same store root.
- **Legacy `dom/<doc_id>/dom-v*.docJSON` consumers:** the final `Document` is still written
  there via `store.put_dom`. Zero consumer change.
- **`app/parser/cli.py`:** keep `--no-ocr`; add optional `--native-concurrency` /
  `--heavy-concurrency` (default `None` => governor auto-derives).
- **`app/processing/cli.py`:** `--concurrency` (doc-level pool) **stays**. Add
  `--native-concurrency N` and `--heavy-concurrency N` (default `None` => auto-derived).
  `ProcessingConfig` gains `native_concurrency:int|None=None` and `heavy_concurrency:int|None=None`.
- **Rollout:** ship behind the existing `ParserConfig` (no flag day). The page pipeline is
  the default path; if a fatal regression appears, the legacy whole-doc `Loaders.load`
  path remains available as a fallback branch (kept, not deleted) gated by a config flag.

---

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Docling version drift** (`page_range`/`ConversionStatus`/`ErrorItem.page_no` API) | Pin the Docling version; add a startup guard test asserting these APIs exist; if missing, mark engine unavailable -> graceful native fallback. |
| **GPU path dormant** (`gpu_free/gpu_per_job` term) | Formula includes the term but gated behind `AcceleratorOptions(device="cuda")`; inactive on CPU box. |
| **Temp-file churn (N writes/doc)** | Single reused `manifest/<doc_id>/src.<ext>` path; heavy engine reads it directly. |
| **Router expected-set vs fitz `page_count`** | Standardize `expected_page_set` on **fitz `len(pdf)`**; if Docling reports a different count, log a mismatch warning but keep fitz count as the required set. |
| **`ProcessPoolExecutor` on Windows / pickling** | Pass only serializable `PageWorkItem` fields; build the engine **inside** the worker (never pickle the engine). |
| **Nested pools** | One shared `Scheduler` (and its heavy `ProcessPoolExecutor`) per process, created once; not per document. |
| **BLAS-thread multiplier defeats budget** | `OMP_NUM_THREADS=1`/`MKL_NUM_THREADS=1` in every heavy process; `F` measured with them set. |
| **Overcommit / swap thrash** | `HEADROOM≈0.80` + process isolation so any OOM is contained to one page job (FAILED, retryable). |

---

## 10. Definition of done / acceptance

1. Both reviewers (Gate 5/6) emit `VERDICT: PASS`.
2. `pytest tests/ -q` green (new unit tests for: `ResourceGovernor.derive_heavy_concurrency`,
   `Planner` expected-set + resume, `PageStore`/`Ledger` round-trip, `DocumentValidator`
   completeness gate, `heavy_docling` `status`/content check, `Assembler` dead-letter on
   exhaustion).
3. Real corpus `C:/Users/Asus/Downloads/test_cases` (12 PDFs + 3 images):
   - **Zero** `std::bad_alloc` / OOM crashes.
   - **Zero silent page loss**: every document reports `actual_pages` vs `expected_pages`;
     any gap is an explicit `failed` + `missing` list, never a fake success.
   - All 15 documents parse with correct routing; final DOM still at `dom/<doc_id>/dom-v*.docJSON`.
4. Knowledge Curator checkpoint at `checkpoints/run/run-2026-08-19-page-centric/checkpoint.md`.
5. Final report at `checkpoints/run/run-2026-08-19-page-centric/final-report.md`.
6. ADR-013 appended to `project_memory/architecture_decisions.md`.

---

## 11. ADR-013 (to append to `project_memory/architecture_decisions.md`)

### ADR-013 — Page-centric execution model + resource-aware scheduling (2026-08-19, run-2026-08-19-page-centric)

**Decision:** Redesign the parser execution model so the **page is the fundamental
processing and durable-storage unit** and the **document is the orchestration unit**.
Adopt a **page-centric pipeline** with (1) a decision-only router (ADR-011) deciding one
band per document, applied uniformly page-by-page; (2) a **`Scheduler`** decoupling a wide
`native_pool` (`ThreadPoolExecutor`: PyMuPDF/enrichment/image/simple) from a **bounded
`heavy_pool` (`ProcessPoolExecutor`)** for Docling/OCR; (3) Docling invoked per page via
`page_range=(p,p)` so peak C++ heap is bounded to one page; (4) a **`ResourceGovernor`**
deriving `heavy_concurrency = f(ram_cap, measured F, headroom, gpu)` from measured RAM/GPU
(not a fixed cap) — "scale by hardware"; (5) a **per-page `PageResult`** + **page store**
(`pages/<doc_id>/p<idx>/page-v<ver>.docJSON`) + **per-document ledger**
(`manifest/<doc_id>/plan.json`) enabling idempotent resume/retry without reparsing done
pages; (6) a **`DocumentValidator`** gate allowing assembly to succeed **only** when
`assembled_page_set == expected_page_set` (established before paging), with dead-letter on
exhausted retries so any loss is explicit, never silent. **Fact** (adopted this run).

**Why:**
- Research established the root cause as **document-length × concurrency C++ heap
  multiplication** (20 `std::bad_alloc` on 3 concurrent whole-doc Docling workers) and that
  Docling **back-fills empty stub pages** while the loader ignored `status`/`page_count`
  → silent loss. `page_range=(p,p)` is a supported knob bounding peak heap to one page;
  per-page `status`+content inspection makes loss detection trivial.
- Research established the GIL prevents `ThreadPoolExecutor` from parallelizing Docling
  (the current single-pool failure mode), and that process isolation + a **persistent
  per-process warmed engine** is required (fresh process per page = N× model warm-up,
  rejected). Decoupling native (wide, GIL-releasing) from heavy (bounded, process-isolated)
  matches the research's "serialized-heavy + wide-native is strictly better" economics.
- The BLAS-thread multiplier (`heavy_concurrency × cpu_count` OpenMP/MKL threads) is
  neutralized by `OMP_NUM_THREADS=1`/`MKL_NUM_THREADS=1` set in every heavy process, with
  `F` measured under those env vars — so the budget holds and scaling is by process count.

**How to apply:**
- New modules under `app/parser/`: `source.py`, `engines/` (`base.py`, `native_pdf.py`,
  `enrichment.py`, `heavy_docling.py`, `image.py`, `simple.py`), `page_result.py`,
  `planner.py`, `storage_pages.py`, `scheduler.py`, `assembler.py`.
- `Extractor.extract` stays a thin synchronous facade; `FilesystemStore` (`raw/`,`dom/`,
  `images/`) layout unchanged; final DOM still written via `put_dom`. Additive dirs `pages/`,
  `manifest/` under the store root.
- `docling_loader` gains `convert_path(path, page, models_dir)` (reads `ConversionResult`,
  no per-page temp) + `get_engine()` (per-process singleton). `DocumentBuilder.build` is
  reused unchanged (pages folded into one `RecoveredDocument`).
- `ProcessingConfig` gains `native_concurrency`/`heavy_concurrency` (None => auto). CLIs
  gain `--native-concurrency`/`--heavy-concurrency`; `--concurrency` (doc-level) retained.

**Challenge (recorded):** page-at-a-time could be slower than a small chunked range if
per-page overhead dominates; mitigated by the persistent per-process engine (no warm-up per
page) and by keeping `native_pool` wide. What would reverse it: a measured corpus where a
bounded chunked range (2–4 pp) is both faster and RAM-safe — revisit only with the same
per-page `status` validation + `heavy_pool` governor. Docling version drift is contained
by a pinned version + startup API guard test; GPU term is dormant pending CUDA enablement.

**Verdict:** evidence-backed; eliminates silent `std::bad_alloc` loss and scales by
hardware. Adopted.

---

ARCHITECTURE: APPROVED