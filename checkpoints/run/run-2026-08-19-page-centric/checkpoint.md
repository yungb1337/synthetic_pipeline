# Run Checkpoint — `run-2026-08-19-page-centric`

**Phase:** Gate 7 (Knowledge Curator, WRITING gate — no application code).
**Date:** 2026-08-19
**Objective:** Redesign `app/parser` so the page is the fundamental processing AND durable-storage unit while the document stays the orchestration unit; eliminate silent page loss from `std::bad_alloc`/OOM via a resource-aware, hardware-scaling pipeline.

## Objective (from `active_objective.md`)
Eliminate silent page loss (`std::bad_alloc` truncation reported as success) by making the
parser page-centric and resource-aware. Router (ADR-011) decides the band per document; the
band is applied page-by-page. Docling is bounded to one page per job
(`page_range=(p,p)`); concurrency is derived from measured RAM, not a fixed cap.

## What each gate produced (verdicts)
- **Gate 1 — Research:** established root cause (document-length × concurrency C++ heap
  multiplication, 20 `std::bad_alloc`; Docling back-fills empty stubs; loader ignored
  `status`/`page_count` → silent loss) and the fix (`page_range=(p,p)` + measured-RAM
  governor + persistent per-process engine).
- **Gate 2 — Architecture spec:** full page-centric design (10 sections) → **ARCHITECTURE: APPROVED**.
- **Gate 3/4 — Plan / Implement:** new modules `source.py`, `engines/{base,native_pdf,enrichment,heavy_docling,image,simple}.py`, `page_result.py`, `planner.py`, `storage_pages.py`, `scheduler.py`, `assembler.py`; rewired `extraction.py` + `executor.py`; router unchanged. → **IMPLEMENTED** (engineer-report).
- **Gate 5 — Architecture Review (re-review after 1 fix loop):** all 5 MAJOR + MINOR-6/7/9/10 closed → **VERDICT: PASS**.
- **Gate 6 — Quality Review (re-review after 1 fix loop):** all 5 MAJOR + 3 MODERATE + 5 MINOR closed (verified in code, not just claimed); key regression tests 10/10 green; similarity script reports zero problematic pairs → **VERDICT: PASS**.
- **Gate 7 — Knowledge Curator (this gate):** appended ADR-013; updated `module_status.md`, `questions.md`, `MEMORY.md`; wrote this checkpoint + final report.

## Key artifacts
- `checkpoints/run/run-2026-08-19-page-centric/architecture.md` — ADR-013 source text (full spec, sections 0–11).
- `checkpoints/run/run-2026-08-19-page-centric/engineer-report.md` — VERDICT: IMPLEMENTED; per-issue A1..G5 fix map.
- `checkpoints/run/run-2026-08-19-page-centric/reviews/architecture.md` — VERDICT: PASS.
- `checkpoints/run/run-2026-08-19-page-centric/reviews/quality.md` — VERDICT: PASS.
- `project_memory/architecture_decisions.md` — **ADR-013 appended** (page-centric execution model + resource-aware scheduling).
- `_verif_store_clean/` (clean corpus output) + `_verif_store_clean/batch_report.json` (parsed+norm: 15, skipped: 0, failed: 0).

## Test status
- **pytest:** `204 passed, 1 skipped, 0 failed` (68.4 s). New `HeavyDoclingEngine` test (B1), resume test (D1), and heading-parity regression test (F1) all PASS.
- **Clean corpus** (`C:/Users/Asus/Downloads/test_cases` — 12 PDFs + 3 images):
  - `std::bad_alloc` / OOM count = **0**
  - silent page loss = **0** (every doc `actual_pages == expected_pages`; `missing_pages`, `dead_pages`, `failed_pages` all empty; `assembly.status == ok`)
  - docs parsed+norm = **15** (0 skipped, 0 failed; all 15 DOM files at `dom/<doc_id>/dom-v*.docJSON`)

## Canonical DOM contract preserved (confirmed)
- `Document`/`Page`/`Block` schemas **unchanged** — `page_result.py` (`PageResult`/`PageStatus`) is purely additive.
- `Extractor.extract(data, filename="", sha256=None, resume=False) -> ParseOutcome` signature + report keys unchanged (added `expected_pages`/`actual_pages`/dead/failed/missing arrays, all additive).
- `FilesystemStore` (`raw/`,`dom/`,`images/`) layout unchanged; `pages/` + `manifest/` are additive.
- Router (ADR-011) unchanged and decision-only.
- `DocumentBuilder.build` reused unchanged; no `app/` code was touched by this gate.

## What's left
Nothing. All definition-of-done items are met (both reviewers PASS, pytest green, clean
corpus verified, ADR-013 appended, checkpoint + final report written). Ready for the final
human-facing report.
