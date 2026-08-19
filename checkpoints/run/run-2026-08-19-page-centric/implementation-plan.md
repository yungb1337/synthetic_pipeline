# Implementation Plan — Page-centric parser execution model (ADR-013)

**Run:** `run-2026-08-19-page-centric` · **Gate:** TECHNICAL-PLANNER (Gate 3)
**Source contract:** `architecture.md` (`ARCHITECTURE: APPROVED`), `research.md`
**Hard constraints (must hold):**
- `Extractor.extract(data, filename, sha256)` signature, `ParseOutcome` shape, and report keys are **UNCHANGED** (additive `expected_pages`/`actual_pages` only).
- `DocumentBuilder.build(rec, doc_id, sha)` is **reused as-is** (no schema change).
- `FilesystemStore` layout `raw/`, `dom/`, `images/` **untouched**; `pages/`, `manifest/` are additive.
- `RoutingDecision` and the ADR-011 router are **unchanged** (decision-only).
- Heavy Docling engine is **built inside the worker process** — never pickled. Only `PageWorkItem` (str/int/bool) crosses the `ProcessPoolExecutor` boundary.
- Reuse existing helpers (`Loaders._pdf`/`_image`/`_text`…, `docling_loader._map_item/_map_table/_map_image/_recover_formula_text`, `ocr.ocr_bytes`, `fitz_metadata`). Refactor/extract; **do not duplicate** extraction/OCR/mapping logic.

---

## Build order (phases + dependency graph)

```
Phase A  Foundations (no behavior change yet, pure additive types)
  T1 page_result.py            (PageStatus, PageResult, checksum, (de)serialize)
  T2 source.py                 (SourceManifest, SourceScan.scan)
  T3 loaders/docling_loader.py (additive: get_engine(), convert_path())   [needs T1]
  T4 engines/base.py           (PageEngine Protocol)                       [needs T1]
  T5 engines/native_pdf.py     (NativePdfEngine; refactor Loaders._pdf)   [needs T1,T4]
  T6 engines/enrichment.py     (EnrichmentEngine)                         [needs T1,T4,T5]
  T7 engines/heavy_docling.py  (HeavyDoclingEngine; inspects status)      [needs T1,T3,T4]
  T8 engines/image.py          (ImageEngine)                              [needs T1,T4]
  T9 engines/simple.py         (SimpleEngine)                            [needs T1,T4]
  T10 planner.py               (ExecutionPlan, PageWorkItem, Planner)     [needs T1,T2]
  T11 storage_pages.py         (PageStore, Ledger)                        [needs T1]
  T12 scheduler.py             (ResourceGovernor, Scheduler, pools)       [needs T1,T5-T9,T11]
  T13 assembler.py             (DocumentValidator, Assembler)            [needs T1,T10,T11]
  T14 extraction.py rewire     (thin facade)                             [needs T2,T10-T13]
  T15 executor.py + config.py  (shared Scheduler per process)            [needs T12,T14]
  T16 CLIs                     (--native-concurrency/--heavy-concurrency) [needs T14,T15]
  T17 tests                    (unit + corpus verification)              [needs T1-T16]
```

Dependencies land first (exactly the order in the contract): T1 → T2 → T3 → T4 → T5–T9 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17. Tasks T5–T9 are independently implementable once T1+T4 exist (parallelizable within the team).

---

## Phase A — Foundation types

### T1 · `app/parser/page_result.py`  (NEW)
- **Responsibility:** Define the per-page result contract — the durable + processing unit.
- **Key types/functions:**
  - `PageStatus` (Enum): `PENDING | OK | PARTIAL | FAILED | DEAD`.
  - `PAGE_SCHEMA_VERSION = "v0.1.0"`.
  - `@dataclass PageResult`: `doc_id, page_index, route, status:PageStatus, blocks:list[RecoveredBlock], tables:list[RecoveredTable], images:list[RecoveredImage], annotations:list[RecoveredAnnotation], content_present:bool, errors:list[dict], engine_version, docling_version, source_hash, checksum, timings, page_sizes:dict`.
  - `to_recovered_slice() -> tuple(blocks,tables,images,annotations)` — the per-page lists consumed by `Assembler`.
  - `compute_checksum() -> str` — sha256 over canonical JSON of `{blocks,tables,images,annotations,page_index}` (idempotent dedup. Reuse `hashlib`).
  - `to_dict()/from_dict()` + `to_json()/from_json()` for `page-v<ver>.docJSON`.
  - `content_present` = `bool(blocks) or any(t.rows for t in tables) or any(t.cells...)` — i.e. ≥1 block/table-cell/figure recovered.
- **Reuse:** `RecoveredBlock/Table/Image/Annotation` from `app.parser.parts`; `hashlib`.
- **Dependencies:** none.
- **Acceptance test:** unit — construct a `PageResult` with 1 block + 1 table; assert `content_present is True`, `checksum` stable across two equal instances, `from_json(to_json(x)) == x` (round-trip), `to_recovered_slice()` returns the parts.

### T2 · `app/parser/source.py`  (NEW)
- **Responsibility:** Establish `expected_page_set` **before paging** + write ONE reusable source file.
- **Key types/functions:**
  - `@dataclass SourceManifest`: `doc_id, source_hash, expected_page_set:list[int], page_count, slug, mime, declared_extension, probe, page_sizes:dict[int,(w,h)], src_path, metadata:dict`.
  - `class SourceScan:` `scan(data:bytes, filename:str, store:Store) -> SourceManifest`.
    - `detected = detection.detect(data, filename)` (reuse `app.parser.detection.detect`).
    - For PDF: `doc = fitz.open(stream=data)` (reuse `fitz`); `page_count=len(doc)`, `page_sizes={i:(w,h) for i,page}`; `expected_page_set=list(range(page_count))`; `doc.close()`. For others: `expected_page_set=[0]`, `page_count=1`, `page_sizes={}` (images get size at engine time).
    - `doc_id = f"d-{sha256(data).hexdigest()[:16]}"`; `source_hash = sha256(data).hexdigest()`.
    - Write bytes **once** to `<store.root>/manifest/<doc_id>/src.<declared_extension or slug or "bin">` (reuse `Store` root; create dirs). Return `src_path`.
  - **Do NOT** read metadata beyond cheaply-available; full PDF `fitz_metadata` is folded at assembly (per §3.1).
- **Reuse:** `detection.detect`, `fitz`, `Store` (for root + mkdir), `app.parser.loaders._pdfmeta.fitz_metadata` is NOT called here.
- **Dependencies:** T1 (uses `Recovered*` only indirectly; no hard dep, but keep order).
- **Acceptance test:** unit — feed a 3-page fitz-built PDF; assert `page_count==3`, `expected_page_set==[0,1,2]`, `src_path` file exists and equals `data`, `page_sizes` has 3 entries.

### T3 · `app/parser/loaders/docling_loader.py`  (ADDITIVE)
- **Responsibility:** Add per-process engine accessor + per-page `convert` that returns the `ConversionResult` (no temp file, no status-blindness).
- **Key functions to ADD (keep `parse()`/`_convert()` intact for the legacy non-page path):**
  - `def get_engine():` returns `_engine` (build via existing `engine_available()`), or `None`. This is the **per-process singleton** the heavy worker reuses.
  - `def convert_path(path:str, page:int, models_dir:str|None=None) -> object|None:` sets `DOCLING_MODELS_PATH` if `models_dir`, gets engine via `get_engine()`; if `None` → return `None`; else `return converter.convert(path, page_range=(page+1, page+1))` (**1-based**; direct on `path`, NO temp file). Returns the `ConversionResult`.
  - **Startup guard (NEW `docling_guard()`):** asserts `hasattr(ConversionResult,'status')`, `hasattr(result,'errors')`, `ErrorItem` has `page_no`, and `page_range` kwarg exists on `convert` (introspect signature). Returns `True`/`False`; cached. `engine_available()` should call it; if `False`, mark engine unavailable so callers fall back to native.
- **Reuse:** existing `_engine`, `_build_converter`, `engine_available`; `ConversionResult`/`ErrorItem` from installed `docling`.
- **Dependencies:** T1 (none strictly, but convert_path consumes `PageWorkItem` semantics later).
- **Acceptance test:** unit (guarded on docling availability via `pytest.importorskip("docling")`): `convert_path` on a fixture PDF with `page=0` returns a `ConversionResult` whose `.document.pages` has exactly 1 page; `docling_guard()` returns `True` when docling present.

---

## Phase B — Page engines

### T4 · `app/parser/engines/base.py`  (NEW)
- **Responsibility:** The contract every engine satisfies.
- **Key types:**
  - `class PageEngine(Protocol):` attribute `route_band:str` (one of `native|enrichment|docling|image|simple`); `def process(self, item:"PageWorkItem") -> "PageResult": ...`.
  - Engines take `PageWorkItem` (carries `src_path`, not raw bytes) and return `PageResult`. They must **not** touch the DOM, the `Ledger`, or the `Store` (pure transform).
- **Reuse:** imports `PageResult` (T1), `PageWorkItem` (T10 — forward ref OK).
- **Dependencies:** T1.
- **Acceptance test:** structural — `NativePdfEngine`/`HeavyDoclingEngine`/`ImageEngine`/`SimpleEngine`/`EnrichmentEngine` each expose `route_band` and a `process(item)->PageResult` (verified by importing in the engines' tests).

### T5 · `app/parser/engines/native_pdf.py`  (NEW + refactor `Loaders._pdf`)
- **Responsibility:** Per-page PyMuPDF extractor; **single source of truth** for native PDF text/font/bold + `find_tables` + images.
- **Key functions:**
  - `class NativePdfEngine(PageEngine):` `route_band="native"`; `def __init__(self, config:ParserConfig):`.
  - `def extract_page(self, src_path:str, page_index:int) -> PageResult:` opens `fitz.open(src_path)`, gets `page=doc[page_index]`, extracts text blocks w/ font+bold (reuse the exact loop from `Loaders._pdf`), `find_tables()` (gated by `config.pdf_extract_tables`), images (`page.get_images`/`extract_image`/`get_image_rects`, reuse `hashlib`, `_MIME` mapping from `loaders.py`). Returns `PageResult(status=OK, content_present=bool(≥1 part))`. A genuinely blank page → `OK` + empty parts + no error.
  - `def process(self, item:PageWorkItem) -> PageResult:` calls `extract_page(item.src_path, item.page_index)`.
- **Refactor `Loaders._pdf` (in `loaders/loaders.py`):** replace its body with: open `fitz.open(stream=data)` for `page_count` + `fitz_metadata`, then loop pages calling `NativePdfEngine(config).extract_page` (writing to a temp `src`? No — `_pdf` has `data` in memory, so open `fitz.open(stream=data)` for the whole doc is fine for the legacy path), gather blocks, run the **median font-size heading classification** pass (was inline in `_pdf`) across all gathered blocks, attach metadata, return `RecoveredDocument`. This removes the duplicated extraction loop.
- **Reuse:** entire extraction loop, `find_tables`, image extraction, `_MIME` from `loaders.py`; `fitz_metadata` from `_pdfmeta`.
- **Dependencies:** T1, T4.
- **Acceptance test:** unit — `NativePdfEngine` on a fixture multi-page PDF: `extract_page(pdf, 0)` → `PageResult` with `status==OK` and `content_present`; the old `Loaders(config)._pdf(data,...)` still returns a `RecoveredDocument` with equal block counts (no behavior regression). Existing `test_native_pdf_loader_carries_metadata` stays green.

### T6 · `app/parser/engines/enrichment.py`  (NEW)
- **Responsibility:** Per-page ADR-012 variant — native extract, OCR only the page if it has zero text blocks.
- **Key functions:**
  - `class EnrichmentEngine(PageEngine):` `route_band="enrichment"`; `def __init__(self, config)`.
  - `def process(self, item):` `res = NativePdfEngine(self.config).process(item)`; if `res.content_present` (has ≥1 text block) → return `res`. Else (zero text blocks) and `self.config.ocr_enabled`: render **one** page with fitz (`page.get_pixmap()` → PNG bytes) and call `ocr.ocr_bytes(png)` (reuse `app.parser.ocr.ocr_bytes`); append `RecoveredBlock(page=item.page_index, source="ocr", ocr_engine=ocr.engine_name())`; set `content_present` accordingly. One render per scanned page (matches ADR-012).
- **Reuse:** `NativePdfEngine` (T5), `ocr.ocr_bytes`, `fitz`.
- **Dependencies:** T1, T4, T5.
- **Acceptance test:** unit — on a fixture **image-only PDF page** (text absent), `EnrichmentEngine` returns `PageResult` with `source=="ocr"` block(s) and `content_present==True`; on a text page it returns native blocks and performs no OCR.

### T7 · `app/parser/engines/heavy_docling.py`  (NEW — runs INSIDE heavy worker)
- **Responsibility:** The silent-loss fix — `page_range=(p,p)` + explicit `status`/`content` inspection; maps one page via existing helpers.
- **Key functions:**
  - `class HeavyDoclingEngine(PageEngine):` `route_band="docling"`; `def __init__(self, config)`.
  - `def process(self, item):` `result = docling_loader.convert_path(item.src_path, item.page_index, item.models_dir)`. If `result is None` (engine unavailable) → `PageResult(status=FAILED, errors=[{page_no:item.page_index+1, category:"engine_unavailable", message:"docling not available"}])`.
    - **Status check:** `status = result.status`; read `result.errors` (list of `ErrorItem` w/ `page_no`, `category`). If `status` is `FAILURE` (or not in {SUCCESS,PARTIAL_SUCCESS}) → `PageResult(status=FAILED/ PARTIAL, errors=[...])` — **never** synthesize success from an empty stub.
    - **Content check:** the `result.document` must contain page `item.page_index+1` with ≥1 block/table/cell. If empty/stub → `PARTIAL|FAILED` with explicit error.
    - **Map (scope to one page):** iterate `result.document.iterate_items()`, keep only items whose `prov.page_no == item.page_index+1`; call existing `docling_loader._map_item/_map_table/_map_image` (which append into a `RecoveredDocument`); then `docling_loader._recover_formula_text(item_src_bytes, rec)` — but we only have `src_path`; read bytes via `open(src_path,'rb').read()` (or pass `data`). Set `rec.reading_order_authoritative=True`, `rec.docling_version`, `rec.layout_model` (reuse the existing `engine_name()`/`_layout_model_name` logic from `docling_loader`).
    - Build `PageResult(status=OK if content else PARTIAL, content_present=..., blocks=rec.blocks, tables=rec.tables, images=rec.images, docling_version=rec.docling_version, engine_version=rec.docling_version)`.
- **Reuse:** `docling_loader.convert_path` (T3), `_map_item/_map_table/_map_image/_recover_formula_text` (existing), `engine_name`, `_layout_model_name` (existing), `RecoveredDocument`.
- **Dependencies:** T1, T3, T4.
- **Acceptance test:** unit (guarded `importorskip("docling")`) — on a fixture PDF, `process` for page 0 returns `OK` with ≥1 block and `reading_order_authoritative` carried; a **forced FAILURE** (monkeypatch `convert_path` to return a `ConversionResult` with `status=FAILURE`) yields `PageResult.status==FAILED` and a non-empty `errors` (never `OK`).

### T8 · `app/parser/engines/image.py`  (NEW)
- **Responsibility:** Standalone image OCR (one page per image file).
- **Key functions:**
  - `class ImageEngine(PageEngine):` `route_band="image"`; `def __init__(self, config)`.
  - Extract a **shared helper** `def _image_bytes(data:bytes, config) -> RecoveredDocument` in `loaders/loaders.py` (refactor of `Loaders._image` so both `Loaders._image` and `ImageEngine` call it — no duplication). `ImageEngine.process(item)` reads `data=open(item.src_path,'rb').read()`; if `not config.ocr_enabled` → empty `PageResult(OK)`; else `ocr.ocr_bytes(data)` → `PageResult(page_index=0, status=OK, blocks=RecoveredBlock(source="ocr",...))`.
- **Reuse:** `ocr.ocr_bytes`, refactored `_image_bytes`.
- **Dependencies:** T1, T4.
- **Acceptance test:** unit — on a fixture PNG with text, `ImageEngine.process` returns `OK` with ≥1 `source=="ocr"` block; with `ocr_enabled=False` returns `OK` and zero blocks.

### T9 · `app/parser/engines/simple.py`  (NEW)
- **Responsibility:** All inherently single-page formats (txt/csv/tsv/json/xml/html/md/docx/xlsx).
- **Key functions:**
  - `class SimpleEngine(PageEngine):` `route_band="simple"`; `def __init__(self, config)`.
  - `def process(self, item):` read `data=open(item.src_path,'rb').read()`; re-`detect` via `detection.detect(data, item.src_path)` (cheap, accurate; avoids adding slug to `PageWorkItem`); dispatch to `Loaders(config)._text/_delimited/_json/_xml/_html/_markdown/_docx/_xlsx` (reuse exactly). Return `PageResult(page_index=0, status=OK, blocks=rec.blocks, tables=rec.tables, images=rec.images, content_present=bool(blocks or tables))`.
- **Reuse:** `Loaders._text/_delimited/_json/_xml/_html/_markdown/_docx/_xlsx`, `detection.detect`.
- **Dependencies:** T1, T4.
- **Acceptance test:** unit — CSV fixture → `SimpleEngine.process` returns `OK` with 1 table; markdown fixture → `OK` with heading + paragraph blocks; `page_index==0`.

---

## Phase C — Orchestration

### T10 · `app/parser/planner.py`  (NEW)
- **Responsibility:** Build one `PageWorkItem` per expected page; write ledger; support resume.
- **Key types/functions:**
  - `@dataclass PageWorkItem`: `doc_id, source_hash, src_path, page_index, route, decision, models_dir, ocr_enabled, attempt=0`. (Add `route` band string; `decision` optional `RoutingDecision`.)
  - `@dataclass ExecutionPlan`: `doc_id, source_hash, sha, route, decision, detected_type, mime, declared_extension, probe, expected_page_set:list[int], page_count, page_sizes:dict, metadata:dict, config_snapshot:dict, work_items:list[PageWorkItem]`.
  - `class Planner:` `plan(manifest:SourceManifest, route:str|None, decision, config:ParserConfig) -> ExecutionPlan`:
    - Derive **band** from `(manifest.slug, route, decision)`:
      - `route=="docling"` → `"docling"`; `route=="native"` → `"native"`; `decision` present → `decision.route` (native/enrichment/docling); else (auto, no decision) → `"image"` if slug in image set else `"simple"`.
      - If band would be `"docling"` but `docling_loader.engine_available()` is `False` → downgrade band to `"native"`/`"enrichment"` (graceful degrade; never crash). Log a warning.
    - One `PageWorkItem` per `expected_page_set` element, each tagged with `band`. `models_dir=config.docling_models_dir`, `ocr_enabled=config.ocr_enabled`.
    - **Resume:** `Ledger.load_plan(doc_id)`; for pages already `OK` in ledger **and** `PageStore.page_exists(doc_id, p)` → exclude from `work_items`.
    - `Ledger.write_plan(doc_id, plan)` with all pages `PENDING` (or `OK` if resumed).
- **Reuse:** `SourceManifest` (T2), `docling_loader.engine_available`, `Ledger` (T11), `RoutingDecision`.
- **Dependencies:** T1, T2, T11 (Ledger must exist; implement T11 first or import lazily).
- **Acceptance test:** unit — PDF manifest with `expected_page_set=[0,1,2]` → `plan.work_items` has 3 items, all `route` band correct; after writing a ledger marking page 0 `OK` + `PageStore.put_page` for page 0, a second `plan()` call returns `work_items` with only pages 1,2 (resume skip).

### T11 · `app/parser/storage_pages.py`  (NEW)
- **Responsibility:** Additive page store + per-document ledger under the same store root.
- **Key types/functions:**
  - `class PageStore:` `def __init__(self, root:str)` (mirror `FilesystemStore` dir creation; do NOT erase `raw/dom/images`).
    - `put_page(doc_id, page_index, result:PageResult) -> str`: write `<root>/pages/<doc_id>/p<page_index>/page-<PAGE_SCHEMA_VERSION>.docJSON` (versioned; same-version overwrite; prior versions retained — ADR-008 semantics). Returns key.
    - `get_page(doc_id, page_index) -> PageResult|None`.
    - `page_exists(doc_id, page_index) -> bool`.
  - `class Ledger:` `def __init__(self, root)`.
    - `write_plan(doc_id, plan:ExecutionPlan) -> str`: `<root>/manifest/<doc_id>/plan.json` per §3.11 schema (`doc_id, source_hash, route, expected_page_set, page_count, created_at, config_snapshot, pages:{<i>:{status,checksum,engine,attempts,errors}}, assembly:{status,assembled_page_set,report}`).
    - `load_plan(doc_id) -> dict|None`.
    - `update_page(doc_id, page_index, status, checksum, engine, attempt, errors)`.
    - `update_assembly(doc_id, status, assembled_set, report)`.
- **Reuse:** `PageResult` (de)serialize (T1); `json`.
- **Dependencies:** T1.
- **Acceptance test:** unit — `put_page` then `get_page` round-trips equal `PageResult`; `Ledger.write_plan`/`load_plan` round-trip; `update_page`/`update_assembly` mutate the persisted dict correctly.

### T12 · `app/parser/scheduler.py`  (NEW)
- **Responsibility:** `ResourceGovernor` (hardware-derived concurrency) + `Scheduler` (native `ThreadPool` + heavy `ProcessPool`, backpressure, per-page persist, exception containment).
- **Key types/functions:**
  - `class ResourceGovernor:` 
    - `def __init__(self, config, shared_value=None)` — `shared_value` is a `multiprocessing.Value('d', 0.0)` for measured `F`.
    - `measure_footprint() -> float|None`: if `docling` unavailable → `None`. Else build engine (via `docling_loader.engine_available()`) and, using a **synthetic** small + large PDF generated with `fitz` (temp file), call `docling_loader.convert_path`; sample `psutil.Process().memory_info().rss` peak around the conversions; publish max to `shared_value`; return it. **Guard `import psutil` with `except ImportError` → treat as unavailable → `F=None`.**
    - `derive_heavy_concurrency(ram_cap, base_overhead, F) -> int`: `max(1, floor((usable - base_overhead)/F))` where `usable = min(psutil.virtual_memory().total, cgroup_max) * HEADROOM(0.80)`; cgroup cap read from `/sys/fs/cgroup/memory.max` (v2) or `.../memory.limit_in_bytes` (v1) when present; `base_overhead` = orchestrator + native pool RSS estimate (default to a safe constant if `psutil` missing). If `F is None` (docling absent or no psutil) → return `1`.
    - `periodic_recheck()`: re-derive from `psutil.virtual_memory().available`; **downward-only** adjustment of a live `self._heavy_concurrency` (applied to next pool cycle; never spawn mid-flight upward surges).
  - `class Scheduler:` one shared instance per process.
    - `def __init__(self, config:ParserConfig, native_concurrency:int|None=None, heavy_concurrency:int|None=None)`:
      - `native_pool = ThreadPoolExecutor(max_workers = native_concurrency or min(32, (cpu_count or 4)*2))`.
      - Heavy: probe `F` via `ResourceGovernor` (spawns a 1-worker probe pool with `initializer` that warms engine + measures, reads `shared_value`); `heavy_concurrency = heavy_concurrency or governor.derive_heavy_concurrency(...)`.
      - `heavy_pool = ProcessPoolExecutor(max_workers=heavy_concurrency, initializer=_heavy_initializer, initargs=(models_dir,))` where `_heavy_initializer` sets `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `TORCHDYNAMO_DISABLE=1`, `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`, then warms `docling_loader.engine_available()`. (Module-level functions so they pickle on Windows `spawn`.)
    - `def run_plan(self, plan:ExecutionPlan) -> list[PageResult]`:
      - For each `PageWorkItem`, choose pool by `route` band: native/enrichment/image/simple → `native_pool`; docling → `heavy_pool`. Use **module-level** worker functions `_run_native(item, config, band)` / `_run_heavy(item, config)` (the latter imports `HeavyDoclingEngine` and calls `process`; engine lives in worker process).
      - **Backpressure:** submit with a bounded `Semaphore`; as each future completes (`as_completed`), catch `future.exception()` (contained → that page → `PageResult(status=FAILED, errors=[{..., message:traceback}])`), then `PageStore.put_page` + `Ledger.update_page`. Collect results.
      - Returns `list[PageResult]` (also durable in store for resume/assembly).
    - `def close(self)` / context manager.
  - **Windows/spawn note:** everything crossing the pool boundary (`PageWorkItem`, `config`, `src_path`) must be picklable; engines are **never** passed — built inside workers.
- **Reuse:** `NativePdfEngine/EnrichmentEngine/ImageEngine/SimpleEngine/HeavyDoclingEngine` (T5–T9), `PageStore`+`Ledger` (T11), `docling_loader`, `psutil` (guarded), `multiprocessing`, `concurrent.futures`.
- **Dependencies:** T1, T5–T9, T11.
- **Acceptance test:** unit — `ResourceGovernor.derive_heavy_concurrency` with **mocked** `ram_cap`/`base_overhead`/`F` returns `max(1, floor(...))`; returns `1` when `F is None`; a small in-process `run_plan` over a 2-page fixture (native band) returns 2 `OK` `PageResult`s and persists them; a worker that raises yields a `FAILED` `PageResult` (no crash).

### T13 · `app/parser/assembler.py`  (NEW)
- **Responsibility:** `DocumentValidator` completeness gate + fold pages → one `RecoveredDocument` → `DocumentBuilder.build` → `Store.put_*`, with retry/backoff and dead-letter.
- **Key types/functions:**
  - `class DocumentValidator:`
    - `assembled_page_set(results) -> set[int]`: `{r.page_index for r in results if r.status in (OK,PARTIAL) and r.content_present}`.
    - `is_complete(expected, assembled) -> bool`: `set(assembled)==set(expected)`.
  - `class Assembler:`
    - `def __init__(self, config, page_store:PageStore, ledger:Ledger, file_store:Store, scheduler:Scheduler)`.
    - `def assemble(self, plan, results, src_path) -> (Document|None, report:dict)`:
      - Build the full `PageResult` set: for each `p in plan.expected_page_set`, take from `results` (by `page_index`) **or** `page_store.get_page(doc_id, p)` (resume/retry pages not re-run).
      - **Retry loop:** if `not is_complete`: `missing = set(expected) - assembled`; if `attempt >= config.max_retries` (or `max_retries` from a new `ParserConfig.page_retries`, default 3) → break to dead-letter; else build new `PageWorkItem`s (`attempt+1`), `time.sleep(backoff)`, call `scheduler.run_plan(sub_plan)` → merge into results → repeat.
      - **Fold:** `rec = RecoveredDocument(detected_type=plan.detected_type, mime=plan.mime, declared_extension=plan.declared_extension, probe=plan.probe, page_sizes=plan.page_sizes, reading_order_authoritative=any(r.docling_version for r in results), docling_version/layout_model gathered from results, routing=plan.decision)`; append blocks/tables/images from each page slice (in `page_index` order); for PDFs also run `fitz_metadata` on the source (`open(src_path).read()`) to fill title/author/…; `rec.timings` aggregated.
      - Persist images: `for img in rec.images: img.storage_ref = file_store.put_image(doc_id, img)` (exactly as current `extraction.py`).
      - `doc = DocumentBuilder.build(rec, doc_id, plan.sha)`; `file_store.put_dom(doc_id, doc)`; `file_store.put_raw(doc_id, sha, data, slug)` (read `data` from `src_path`); `ledger.update_assembly(doc_id, "ok", assembled_set, report)`.
      - **Dead-letter:** after exhaustion, missing pages → `ledger.update_page(..., status="DEAD", ...)`; `ledger.update_assembly(doc_id, "failed", assembled_set, report)`; `report["actual_vs_expected"] = {"expected":len(expected), "actual":len(assembled), "missing":sorted(missing)}`; return `(None, report)` (loud, never a fake success).
      - Returns `(doc, report)`; `report` carries `expected_pages`, `actual_pages`, `route`, `blocks/tables/images` counts, `dom_key`, `raw_key`.
- **Reuse:** `DocumentBuilder.build` (unchanged), `FilesystemStore.put_image/put_dom/put_raw`, `fitz_metadata`, `PageStore`/`Ledger`, `PageResult.to_recovered_slice`.
- **Dependencies:** T1, T10, T11, T12.
- **Acceptance test:** unit — `DocumentValidator.is_complete` true when assembled==expected, false otherwise; `Assembler` on a 3-page OK result set folds into a `Document` with 3 pages and correct block counts; a forced missing page (only 2 of 3 `OK`) with `max_retries=0` → `assemble` returns `(None, report)` with `actual_vs_expected` listing the missing page and ledger `assembly.status=="failed"` (dead-letter, no silent loss).

---

## Phase D — Rewire facades & CLIs

### T14 · `app/parser/extraction.py`  (REWIRE — signature unchanged)
- **Responsibility:** `Extractor.extract` becomes a thin facade: `detect → route → SourceScan.scan → Planner.plan → Scheduler.run_plan → Assembler.assemble → Store + event`.
- **Changes:**
  - `Extractor.__init__(self, config, store, events=None, scheduler=None, native_concurrency=None, heavy_concurrency=None)`:
    - Build/hold one shared `Scheduler` (module-cached per process via a helper `get_shared_scheduler(config, native_concurrency, heavy_concurrency)`), `Planner`, `PageStore(store.root)`, `Ledger(store.root)`, `Assembler(config, page_store, ledger, store, scheduler)`. Keep `self.loaders`/`self.builder` for legacy fallback path only (kept, not deleted).
  - `extract(data, filename, sha256)` body:
    1. `sha`, `doc_id` as today.
    2. `detected = detection.detect(...)`; unresolved/too-large → same early returns.
    3. `route, decision = self._compute_route(data, detected)` (UNCHANGED behavior).
    4. `manifest = SourceScan.scan(data, filename, self.store)`.
    5. `plan = self.planner.plan(manifest, route, decision, self.config)`.
    6. `results = self.scheduler.run_plan(plan)`.
    7. `doc, report = self.assembler.assemble(plan, results, manifest.src_path)`.
    8. If `doc is None` → `ParseOutcome(doc_id, "failed", None, detected, report)`.
    9. Else build the SAME `report` (elapsed_ms, timings, blocks, tables, images, pages, ocr, route, dom_key, raw_key, **expected_pages, actual_pages**) + `self._emit("document.parsed.v1", doc_id, {..., "expected_pages":..., "actual_pages":...})`. Return `ParseOutcome(doc_id, "parsed", doc, detected, report)`.
  - **Keep** `_compute_route`, `_get_router`, `_emit` unchanged.
- **Reuse:** `SourceScan` (T2), `Planner` (T10), `Scheduler` (T12), `Assembler` (T13), `detection`, `Store`.
- **Dependencies:** T2, T10, T11, T12, T13.
- **Acceptance test:** existing `test_parser.py` + `test_docling_loader.py` suite stays GREEN (the public contract is unchanged; routes still resolve). New happy-path check that a 2-page PDF yields `ParseOutcome.ok` with `report["expected_pages"]==report["actual_pages"]==2` and `dom_key` present.

### T15 · `app/processing/executor.py` + `app/processing/config.py`  (REWIRE)
- **Responsibility:** One shared `Scheduler` per process injected into each `Extractor`; add concurrency knobs.
- **Changes (`config.py`):**
  - `ProcessingConfig` gains `native_concurrency:int|None=None` and `heavy_concurrency:int|None=None` (None ⇒ auto-derived by governor).
- **Changes (`executor.py`):**
  - `ParseNormalizePipeline.__init__` builds a **single** `Scheduler` (via `get_shared_scheduler(parser_cfg, cfg.native_concurrency, cfg.heavy_concurrency)`) and injects it into each `Extractor`: `Extractor(parser_cfg, store, events=..., scheduler=self._scheduler)`.
  - `BatchWorker` keeps its doc-level `ThreadPoolExecutor(max_workers=config.concurrency)` (unchanged). Retries/backoff/manifest logic unchanged. Ensure `self._scheduler.close()` is called at end of `run()` (or via `atexit`/context) so heavy pool shuts down cleanly.
- **Reuse:** `Extractor` (T14), `Scheduler` (T12), `get_shared_scheduler`.
- **Dependencies:** T12, T14.
- **Acceptance test:** existing `test_processing.py` stays GREEN; a 2-doc batch run uses the shared scheduler (assert only one `heavy_pool` created — e.g. count `ProcessPoolExecutor` constructions or assert `ParseNormalizePipeline` instances share the same scheduler object).

### T16 · CLIs — `app/parser/cli.py` + `app/processing/cli.py`  (ADDITIVE flags)
- **Responsibility:** Expose `--native-concurrency` / `--heavy-concurrency` (default `None` ⇒ auto); keep `--concurrency`, `--no-ocr`, `--manifest`.
- **Changes (`app/parser/cli.py`):** add `--native-concurrency`/`--heavy-concurrency` (`type=int, default=None`); pass them when constructing the `Extractor` (via `get_shared_scheduler`).
- **Changes (`app/processing/cli.py`):** add the two flags; `replace(cfg, native_concurrency=..., heavy_concurrency=...)`; `--concurrency` (doc-level pool) retained.
- **Reuse:** existing argparse wiring.
- **Dependencies:** T14, T15.
- **Acceptance test:** `python -m app.parser.cli --help` and `python -m app.processing.cli --help` list the new flags; a smoke run with `--heavy-concurrency 1` completes without error.

---

## Phase E — Tests & verification

### T17 · `tests/` (add `tests/test_page_centric.py`; keep existing green)
- **Unit tests (new):**
  - `ResourceGovernor.derive_heavy_concurrency` with mocked ram/F (incl. `F=None → 1`).
  - `Planner` expected-set + resume skip (uses `PageStore`/`Ledger` in `tmp_path`).
  - `PageStore`/`Ledger` round-trip.
  - `DocumentValidator` completeness gate.
  - `HeavyDoclingEngine` status/content check (fixture PDF; `importorskip("docling")` + guard on `docling_guard()`).
  - `Assembler` dead-letter on exhaustion (`actual_vs_expected` present, no silent loss).
- **Regression:** run full existing suite — `test_parser.py`, `test_docling_loader.py`, `test_processing.py` — assert still green (public contracts unchanged).
- **Corpus verification (manual gate):**
  1. `.venv/Scripts/python.exe -m pytest tests/ -q` → green.
  2. Run real corpus `C:/Users/Asus/Downloads/test_cases` (12 PDFs + 3 images) via `app/processing/cli.py`:
     - **Zero** `std::bad_alloc` / OOM crashes.
     - **Zero silent page loss**: every document reports `actual_pages` vs `expected_pages`; any gap ⇒ explicit `failed` + `missing` list (assert no document reports `parsed` with `actual < expected`).
     - All 15 documents parse with correct routing; final DOM present at `dom/<doc_id>/dom-v*.docJSON`.
  3. Assert `pages/<doc_id>/p*/page-v*.docJSON` and `manifest/<doc_id>/plan.json` exist for each doc (idempotent resume artifact).

---

## Pre-flight dependencies (verify before coding)
- **`docling`** present and version-pinned; `docling_guard()` (T3) asserts `ConversionResult.status`, `ErrorItem.page_no`, `page_range` exist — if not, engine marked unavailable and the pipeline degrades to native (never silent).
- **`psutil`** — add to the venv if missing; `ResourceGovernor` must `except ImportError` and fall back to `heavy_concurrency=1` (safe) rather than crashing.
- **`fitz` (PyMuPDF)** — already a dependency; used throughout.

## Risks carried from architecture (already mitigated in plan)
- Heavy engine never pickled (built in worker; only `PageWorkItem`+`config`+`src_path` cross the boundary). ✔ T12
- Nested pools avoided (one shared `Scheduler` per process). ✔ T12/T15
- Temp-file churn avoided (single `manifest/<doc_id>/src.<ext>`; `convert_path` reads it directly). ✔ T2/T3
- BLAS-thread multiplier neutralized (`OMP/MKL_NUM_THREADS=1` in every heavy worker). ✔ T12

---

PLAN: READY