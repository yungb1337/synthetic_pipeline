RESEARCH: COMPLETE

# Research — Page-centric execution model for the MedFactory AI parser

**Run:** `run-2026-08-19-page-centric` · **Gate:** RESEARCH · **Date:** 2026-08-19
**Scope:** Eliminate silent `std::bad_alloc`/page-loss in Docling; make concurrency scale by hardware; page = durable + processing unit, document = orchestration unit.

All claims labeled **Fact | Research | Inference | Recommendation**. Primary source for Docling is the *installed* package at `.venv/Lib/site-packages/docling/` (authoritative for the version this repo actually runs).

---

## 1. Docling memory model

**Fact (from installed source).** `DocumentConverter.convert()` signature, `docling/document_converter.py` lines 440-449:
```
@validate_call(config=ConfigDict(strict=True))
def convert(self, source, headers=None, raises_on_error=True,
 max_num_pages=sys.maxsize, max_file_size=sys.maxsize,
            page_range: PageRange = DEFAULT_PAGE_RANGE) -> ConversionResult:
```
`PageRange = Annotated[Tuple[int,int], AfterValidator(...)]` with `DEFAULT_PAGE_RANGE = (1, sys.maxsize)` (`docling/datamodel/settings.py` lines 10-20). The same `page_range` parameter exists on `convert_all` (lines 505-516).

**Fact.** `page_range` *does* bound how many pages are processed in one call. Two independent enforcements:
- `StandardPdfPipeline._get_expected_page_nos()` (`standard_pdf_pipeline.py` lines 793-800) clips to `conv_res.input.limits.page_range`, and only those `Page` objects are created and fed through the stage pipeline (lines 822-838). Pages outside the range are never produced.
- For the threaded backend, `ThreadedDoclingParseDocumentBackend.__init__` (`docling_parse_backend.py` lines 476-521) computes `requested_page_numbers` from `in_doc.limits.page_range` and passes only those pages to the C++ `DoclingThreadedPdfParser`. The native parser physically never segments the other pages.

**Fact.** The C++ heap is document-length-sensitive. `docling-parse`'s `DecodeConfig` exposes `release_native_memory_every_n_pages` (configurable via `PdfBackendOptions.release_native_memory_every_n_pages`; default 128 in the threaded backend). Its existence is direct evidence that the native/C++ parser *accumulates* per-page native memory and must be explicitly released; it does not auto-free every page. With `page_range=(p,p)` only one page is in the native parser at a time, so native heap is bounded to ~one page.

**Fact (ground truth from run brief).** 3 concurrent whole-document Docling workers produced 20 `std::bad_alloc` crashes in the C++ preprocess stage; the same corpus serially with `--no-ocr` recovered nearly all pages. Root cause = concurrency × Docling native/segmentation heap multiplication, not the corpus.

**Research.** A `DocumentConverter` builds model weights **once per pipeline instance** in `StandardPdfPipeline._init_models()`: layout model, OCR, table-structure, reading-order, heading, enrichment. These weights are *fixed per process* and independent of document length. The variable memory that scales with the document is: (a) the per-page rendered bitmap at `images_scale` (default 2.0 → 4× pixel count), (b) docling-parse per-page segmentation buffers, and (c) C++ `pdfium`/`docling-parse` native page memory retained until `release_native_memory_every_n_pages`. Peak per-worker memory without page bounding grows with the number of pages simultaneously in flight; with `page_range=(p,p)` it collapses to one page's cost.

**Recommendation.** `page_range=(p,p)` is the correct and supported knob to bound peak C++ memory per job to one page. Pair it with `images_scale` kept modest and `release_native_memory_every_n_pages=1` for the heavy path so native memory is reclaimed page-by-page.

---

## 2. GIL & parallelism for heavy ML

**Fact.** CPython's GIL prevents `ThreadPoolExecutor` from parallelizing CPU-bound torch/C++ work. `docs/scale-batch-spec.md` (lines 32-34) already records the lesson — "ThreadPool does NOT speed up CPU-bound Python due [to] the GIL… at real scale the lever is worker **processes**." The current `app/processing/executor.py` (line 113) uses a single `ThreadPoolExecutor` for everything, which is exactly the failure mode.

**Fact.** Docling model initialization is expensive (loads layout transformer + table + OCR models, hundreds of MB and seconds). `app/parser/docling_loader.py` `_build_converter()` builds once and caches in a process-global `_engine` with a lock — good. Trade-offs:
- **(a) Serialized single-threaded heavy pool:** peak memory = `1 × per-engine-footprint`, fully deterministic, no GIL contention, no cross-worker heap multiplication. Throughput is one job at a time but is the *safest* floor.
- **(b) `multiprocessing` / process isolation:** each worker is a separate OS process with its own address space → a hard per-process memory cap; a `std::bad_alloc`/OOM in one process cannot corrupt others and can be retried/rescheduled. GIL is per-process, so CPU work *does* parallelize across processes.
- **(c) Per-process Docling engine:** necessary (the engine must live inside the worker process to be reused), but **must not** mean "spawn a fresh process per page" — that pays N× warm-up. The correct form is a **fixed pool of persistent worker processes, each holding one warmed Docling converter**, serving many page jobs.

**Recommendation.** Deploy heavy work via a bounded `ProcessPoolExecutor` (or a small pool of long-lived worker processes), each process lazily building its singleton Docling engine on first use and reusing it for all subsequent page jobs.

---

## 3. Resource-aware concurrency / hardware scaling

**Fact.** `psutil.virtual_memory()` returns `total`, `available`, `used`, `free`, `percent`, `cached`, `buffers`. `available` is the best "free-for-use" estimate (OS cache can be reclaimed). In a container the effective cap is the cgroup limit: cgroup v2 at `/sys/fs/cgroup/memory.max`, cgroup v1 at `/sys/fs/cgroup/memory/memory.limit_in_bytes`. Read that file when present and take `min(ram_total, cgroup_max)`.

**Fact.** For GPU (only if `AcceleratorOptions` selects CUDA), `torch.cuda.mem_get_info()` returns `(free, total)` bytes; `nvidia-ml-py` (`pynvml`) gives headroom/usage detail. The current box is CPU-only (Docling default `device="auto"` → CPU), so RAM is the dominant bound on this machine; the formula below includes the GPU term for portability.

**Research (formula).** Compute `heavy_concurrency` at startup (and re-check periodically):
```
ram_cap = min(psutil.virtual_memory().total, cgroup_memory_max or +inf)
usable = ram_cap * HEADROOM          # HEADROOM ≈ 0.80 (reserve OS + native pool + orchestrator)
base_overhead = fixed non-heavy RSS (orchestrator + native pool workers)
F = measured per-engine RAM footprint (see below)
heavy_concurrency = max(1, floor((usable - base_overhead) / F))
# if Docling on GPU: also cap by floor(gpu_free / gpu_per_job)
```
Where `F` is **measured, not guessed** (the brief forbids inventing heuristics): warm one Docling engine in a process, parse one representative page, sample `psutil.Process().memory_info().rss` before/after, take the max over a few page sizes (including a large page). Reuse that `F`. As hardware grows, `usable`/GPU grow → `heavy_concurrency` grows → "scales by hardware"; as it shrinks, concurrency shrinks → no hard cap, no OOM.

**Research (pitfalls).**
- **Torch CUDA caching allocator** does not return freed memory to the OS by default; `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` reduces fragmentation OOM. If you size to live `available` with no headroom, transient fragmentation triggers OOM.
- **Overcommit / swap thrash:** exceeding RAM silently goes to swap → catastrophic slowdown rather than a clean OOM. Always leave headroom and prefer process isolation so any OOM is *contained* and the page can be retried on another worker.
- **BLAS thread multiplier (strong, concrete):** each Docling/torch process can spawn `cpu_count` OpenMP/MKL threads, each allocating thread-local buffers — a classic silent memory multiplier that compounds with `heavy_concurrency`. **Recommendation:** in every heavy worker process set `OMP_NUM_THREADS=1` and `MKL_NUM_THREADS=1` (and `DOCLING_` perf `page_batch_concurrency=1`) so one Docling process = one compute thread; scale throughput by *process count*, not thread count.
- **No free-threaded benefit today:** Docling's own `doc_batch_concurrency` warns "No benefit expected without free-threaded python". Keep it 1.

---

## 4. Silent partial results — how Docling signals failure

**Fact.** `ConversionResult` extends `ConversionAssets`. It carries:
- `status: ConversionStatus` — enum `PENDING | STARTED | FAILURE | SUCCESS | PARTIAL_SUCCESS | SKIPPED`.
- `errors: list[ErrorItem]` — each `ErrorItem` has `page_no: int | None` and `category` (e.g. `BACKEND_FAILURE`, `INFERENCE_FAILURE`).

**Fact.** `StandardPdfPipeline._integrate_results()` sets `PARTIAL_SUCCESS` when `0 < success_count < total_expected`, `FAILURE` when complete failure, and appends a per-page `ErrorItem` (with `page_no`) for every failed page. **Crucially**, `_add_failed_pages_to_document()` then injects *empty* `PageItem` stubs into `conv_res.document.pages` for missing pages **to preserve numbering**.

**Fact (the silent-loss trap, confirmed in this repo).** `app/parser/docling_loader.py` `parse()` computes `rec.page_count = len(doc.pages)` and `_convert()` returns only `getattr(result, "document", None)` — it **never reads `result.status` or `result.input.page_count`**. Because Docling back-fills empty stub pages, `len(doc.pages)` can equal the expected count even when pages failed → the loader reports a "complete" 24→10-page doc as success. This is precisely the observed silent data loss.

**Fact (correct detection).** The true expected count is `conv_res.input.page_count` (set from the backend in `InputDocument.__init__`). The successful-page set is `conv_res.pages` (the pipeline's produced `Page` list) plus the set of page numbers whose `document.pages[p]` carries *content* (not an empty stub). A conversion is truncated iff `status in {PARTIAL_SUCCESS, FAILURE}` **or** `len(produced_content_pages) < input.page_count`.

**Recommendation.** At page-centric granularity each page is its own `convert(page_range=(p,p))` call, so detection is trivial per page: assert `status in {SUCCESS, PARTIAL_SUCCESS}` and that the returned `DoclingDocument` has page `p` with at least one block/table/cell. For document assembly, validate `assembled_page_count == expected_page_set` (set of 1..`input.page_count`) before marking success — exactly the run-brief requirement.

---

## 5. RapidOCR footprint

**Fact.** `app/parser/ocr.py` builds RapidOCR once (process-global singleton), an onnxruntime PP-OCRv6 engine (CPU). `app/parser/loaders/enrichment.py` renders exactly one Pixmap per empty page and OCRs it — inherently bounded per page.

**Research.** RapidOCR does not hold a whole-document C++ heap; it processes one image at a time. It is substantially lighter than Docling, whose heavy memory is the **layout transformer + pdfium/docling-parse page segmentation**, not the OCR itself.

**Recommendation.** RapidOCR does **not** require process isolation on its own. Keep it in the heavy pool only because it is invoked *inside* Docling's pipeline when `do_ocr=True`. The enrichment band's per-page RapidOCR is already safely bounded.

---

## 6. Prior art / best practices

**Research (industry patterns).**
- **Ray:** per-actor `num_cpus`/`num_gpus`; `ray.util.ActorPool` for bounded worker pools; backpressure via the object store. Memory: actors are OS processes → isolated heaps.
- **Dask distributed:** `Worker(memory_limit=...)`, automatic spill-to-disk, and a **Nanny** that kills workers exceeding the limit.
- **Celery:** `prefork` pool = separate processes (isolated memory, forked engine); `soft_time_limit`/`time_limit` for containment.
- **Argo / Kubernetes:** resource `requests`/`limits` enforced by cgroups; `OOMKilled` is the hard containment primitive.
- **General:** a *resource governor* = measured footprint + `Semaphore`/bounded `ProcessPoolExecutor`; *backpressure* = bounded queues so producers block instead of piling up work; *per-item durability + resume* = write each intermediate result and keep a ledger of per-item status.

**Research (concurrency-vs-throughput economics).** Serialized-heavy + wide-native is **strictly better** when: (1) the heavy stage is memory-bound and GIL/CUDA-limited so extra heavy concurrency yields little CPU gain, and (2) one heavy job's memory dominates the budget. Then N× heavy concurrency only multiplies peak RAM with near-zero throughput benefit. The native lane (PyMuPDF) releases the GIL during C calls and is cheap, so it scales with core count via a wide `ThreadPoolExecutor`.

**Recommendation.** Decouple `heavy_pool` (bounded `ProcessPoolExecutor`, capped by §3) from `native_pool` (wide `ThreadPoolExecutor`), exactly as the brief directs.

---

## 7. Trade-off matrix & bounding strategy

| Strategy | Peak RAM / worker | N× warm-up? | Resume cost | Throughput | Verdict |
|---|---|---|---|---|---|
| **Document-level** (current): 1 `convert()` whole doc | ~all pages in flight + whole-doc native parser | No (1 load) | full re-parse | high per-doc but **OOM-prone** | **Reject** — root cause |
| **Chunked ranges** (e.g. 4 pp) | ~range size × concurrency | No | re-parse range | medium | Middle; still multiplies |
| **Page-centric** (1 pp/call, persistent warmed engine per process) | **1 page** | **No** if engine reused per process | **trivial (page already stored)** | aggregate = heavy_concurrency | **Adopt** |

**Quantify the N× warm-up hazard (the decisive point).** Docling init loads layout + table + OCR models (hundreds of MB, seconds). A 24-page PDF parsed as 24 separate *process* inits = 24 × (model load cost). Therefore **page-centric only works if the engine is cached per process and reused across all page calls** — i.e. a persistent process pool, never a fresh subprocess per page. The current `docling_loader.py` singleton pattern is correct *within one process*; the fix is to move that singleton into each heavy worker *process* and call it per page with `page_range=(p,p)`.

**Recommendation (the bounding strategy).**
1. `heavy_pool` = bounded `ProcessPoolExecutor(max_workers=heavy_concurrency)`; each worker lazily builds its singleton Docling converter on first page and reuses it.
2. Every heavy job = `converter.convert(source, page_range=(p,p))`; treat the returned `ConversionResult` per §4; write a per-page intermediate DOM immediately (durable unit).
3. `native_pool` = wide `ThreadPoolExecutor` (PyMuPDF, enrichment) — no RAM bound, scales with cores.
4. A **document ledger** records per-page status `{pending, ok, partial, failed}`; assembly succeeds only when `assembled_page_set == expected_page_set`; failures are retried per page without reparsing done pages (idempotent, content-addressed — consistent with ADR-008).
5. `heavy_concurrency = f(available RAM, measured F, GPU)` from §3, recomputed at startup/periodically → "scales by hardware."
6. In each heavy process: `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `images_scale` modest, `release_native_memory_every_n_pages=1`.

---

## Open risks (to flag to the architecture gate)
- **Docling version drift:** the `page_range`/`ConversionStatus`/`ErrorItem.page_no` API is confirmed in the *installed* version; pin the Docling version and add a guard test.
- **PDF image re-open per page:** `convert()` currently writes a temp file per call. One call per page = N temp-file writes; reuse a single temp file path per document or pass a stream to avoid churn.
- **`input.page_count` vs router's expected set:** the expected set must be established *before* paging (from `conv_res.input.page_count` or fitz `len(pdf)`), not inferred post-hoc.
- **GPU path untested here:** the `gpu_free/gpu_per_job` term is dormant on this CPU box but must be wired before enabling `AcceleratorOptions(device="cuda")`.

**Verdict:** The page-centric design with persistent per-process warmed Docling engines, `page_range=(p,p)` bounding, a RAM/GPU-derived `heavy_concurrency`, and explicit `status`/`page_count` validation is the evidence-backed way to eliminate silent `std::bad_alloc` loss and to scale by hardware. The research gate recommends adoption; the Chief Architect owns the decision.