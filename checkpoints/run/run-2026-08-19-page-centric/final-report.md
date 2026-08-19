# Final Report — `run-2026-08-19-page-centric`

**Run id:** `run-2026-08-19-page-centric` · **Date:** 2026-08-19 · **Module:** #1 parser execution model

## Objective
Redesign the parser execution model so the **page is the fundamental processing and
durable-storage unit** while the **document remains the orchestration unit**, eliminating
silent page loss (`std::bad_alloc` truncations reported as success) through a
resource-aware, hardware-scaling pipeline.

## The redesign in one paragraph
The parser was rebuilt around pages, not whole documents. A decision-only router (ADR-011)
still picks one band per document, but that band is now applied page-by-page. Docling is
invoked with `page_range=(p,p)`, bounding peak C++ heap to a single page, and every page is
run in a **bounded `ProcessPoolExecutor`** whose size is derived from *measured RAM* (a
`ResourceGovernor`), never a fixed cap — "scale by hardware." Each page produces an explicit
`PageResult` (OK / PARTIAL / FAILED / DEAD) written to a `PageStore`
(`pages/<doc_id>/p<idx>/page-v<ver>.docJSON`) and recorded in a per-document `Ledger`
(`manifest/<doc_id>/plan.json`). Assembly is allowed to succeed **only** when
`assembled_page_set == expected_page_set` (established *before* paging via `fitz len(pdf)`);
any gap is re-enqueued, and on exhaustion dead-lettered with an explicit
`actual_vs_expected` report — so loss is always loud, never a fake success. The router,
`Extractor.extract()` API, and `Document`/`Page`/`Block` schemas are unchanged; `pages/` and
`manifest/` are additive to the existing store.

## Gate verdicts
- Research: COMPLETE (root cause + fix established).
- Architecture: **APPROVED** (`ARCHITECTURE: APPROVED`).
- Plan: READY.
- Implement: **IMPLEMENTED** (pytest 204/1/0; clean corpus verified).
- Gate 5 Architecture Review: **VERDICT: PASS** (after 1 fix loop).
- Gate 6 Quality Review: **VERDICT: PASS** (after 1 fix loop).

## Test status
- `pytest tests/ -q` → **204 passed, 1 skipped, 0 failed** (68.4 s).
- Clean corpus (`C:/Users/Asus/Downloads/test_cases`: 12 PDFs + 3 images) → **15/15 parsed**,
  **0 `std::bad_alloc`**, **0 silent page loss** (`actual_pages == expected_pages` on every
  doc; `missing`/`dead`/`failed` page arrays empty; `assembly.status == ok`).

## ADR
**ADR-013 — Page-centric execution model + resource-aware scheduling (2026-08-19,
run-2026-08-19-page-centric)** appended to `project_memory/architecture_decisions.md`.

## What changed / where it left off
- **New modules (all additive, `app/parser/`):** `source.py`, `engines/{base,native_pdf,enrichment,heavy_docling,image,simple}.py`, `page_result.py`, `planner.py`, `storage_pages.py`, `scheduler.py`, `assembler.py`.
- **Silent-loss guarantee now enforced:** per-page `status` + `DocumentValidator` gate + dead-letter; `docling_loader.convert_path` inspects `ConversionResult.status`/`errors`.
- **Resource governor:** `heavy_concurrency` derived from measured RAM footprint `F`; `OMP_NUM_THREADS=1`/`MKL_NUM_THREADS=1` neutralize the BLAS-thread multiplier; downward-only periodic re-check.
- **Router unchanged (ADR-011):** band decided per document, applied page-by-page.
- **Preserved:** `Extractor.extract()` signature, `FilesystemStore` raw/dom/images layout, `Document`/`Page`/`Block` schemas, `DocumentBuilder.build`.
- **Left off / open (tracked in `project_memory/questions.md`):** GPU side of the governor (`gpu_free/gpu_per_job`) is **dormant/untested on this CPU box** — needs a CUDA box before trusting it; live `ProcessPoolExecutor` shrink on downward recheck is applied via private `_max_workers` and does **not** force-kill in-flight workers; bounded backpressure relies on fixed-size pools (accepted as sufficient per the fix-loop doc).

**Status: DONE.** All definition-of-done items met; no code remains to write.

---

## Post-run hardening — `extract()` safety net (2026-08-19, same run)

**Trigger (user feedback):** A `parse_folder.py` run over `C:/Users/Asus/Downloads/test_cases`
showed `std::bad_alloc` / `ONNXRuntime ... bad allocation` lines in the output. Inspection of the
output store proved the redesign **WORKED**: 15/15 documents reached `assembly.status == ok` with
`expected_pages == actual_pages` and zero silent loss — the per-page `page_range=(p,p)` bounding
contained those errors instead of crashing or dropping pages.

**Latent gap found + fixed:** `Extractor.extract()` wrapped the per-page `run_plan` → `assemble` →
emit in a **final safety net** (`app/parser/extraction.py`). Because `Planner.plan()` writes the
all-`pending` ledger *before* execution (`planner.py:152`), an uncaught exception between `run_plan`
and the final `ledger.update_assembly()` would otherwise leave a document frozen at `pending` with
no DOM and no `document.parse_failed` event — a silent partial-state hole. The fix:
- Any exception for a doc → every still-`pending` page is marked `FAILED` in the ledger (without
  clobbering pages the scheduler already persisted as OK), the assembly is recorded as `failed`,
  `document.parse_failed` is emitted, and `extract()` returns a `failed` outcome (never propagates).
- New regression test `test_extract_exception_never_leaves_document_pending` (asserts no
  `document.parsed.v1`, a `document.parse_failed` fired, assembly `failed`, and **no page left
  `pending`**).

**Verification after fix:** `pytest tests/` → **all green** (the new test + 25 other page-centric
tests + full suite; 1 pre-existing environmental skip). `scripts/check_similarity.py` → **no pairs
≥0.4**. End-to-end smoke of `scripts/parse_folder.py` on synthetic PDFs → 2/2 parsed, store clean.

This closes the only correctness gap surfaced by the user's run; everything else behaved as designed.
