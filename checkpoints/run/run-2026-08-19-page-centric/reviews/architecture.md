# Architecture Review — Gate 5 RE-REVIEW, run-2026-08-19-page-centric

**Reviewer:** architecture-reviewer (read-only pass)
**Prior verdict:** FAIL → re-review after fix loop R1.

## Verdict
VERDICT: PASS

All five MAJOR findings from the prior FAIL, plus MINOR-6/7/9/10, are genuinely closed.
MINOR-8 accepted as documented (fixed-size pools bound in-flight docling work). All hard
constraints still hold.

## Prior MAJOR findings — verified closed
- **M1 (C5)** `docling_guard()` now wired into `engine_available()` (`docling_loader.py:57-78`); runs cached `docling_guard()` at top, returns False on drift before building engine. `docling_guard()` corrected to pydantic-v2 `model_fields` (`docling_loader.py:1051-1083`). Graceful native degrade at availability time.
- **M2 (B1)** `tests/test_page_centric.py:317-373` — three real guarded tests: FAILURE→FAILED (non-empty errors), empty-stub PARTIAL→PARTIAL/FAILED + content_present False, worker-wrapper convert_path→None→FAILED. Substantive, not stubs.
- **M3 (A3)** `assembler.py:235-241` `elif band == "docling":` routes retry through `HeavyDoclingEngine`; native `else` fallback only for unknown bands. No docling→native downgrade. Verified by `test_assembler_retry_docling_stays_docling`.
- **M4 (C1)** `Scheduler.__init__` (`scheduler.py:243-272`) derives `heavy_concurrency` with `F=None` only — no engine build, no `measure_footprint`. Only reference to `measure_footprint` outside its def is `scheduler.py:59` inside `_heavy_initializer`, publishing to `multiprocessing.Value` `_heavy_f_value` (43-66). Orchestrator never loads Docling on ProcessPool path.
- **M5 (C2)** `Scheduler.run_plan` (`scheduler.py:332-346`) every 4 completed docs reads worker-published F, calls `periodic_recheck`, applies downward-only adjustment. Wired.

## Prior MINOR — verified
- MINOR-6 (C4): `release_native_memory_every_n_pages=1`, `doc_batch_concurrency=1`, `page_batch_concurrency=1` set via setattr/try-except (`docling_loader.py:213-218`).
- MINOR-7 (A1): `assembled_page_set`/`classify` AND `content_present` into OK+PARTIAL membership (`assembler.py:48-71`); `PageResult.content_present` auto-derived (`page_result.py:162-181`).
- MINOR-9 (G4): `build`/`put_dom`/`put_raw` only on success (`assembler.py:174-190`); failed/dead → `report.document=None`, report retained.
- MINOR-10 (D1): `extraction.py:151` passes `resume`; `executor.py:113` `extract(..., resume=True)`; `planner.py:108-153` reads ledger, excludes OK, reschedules FAILED/DEAD attempt+1, no clobber. Verified by resume tests.

## Additional fix-loop items verified
- A2: corrupt PDF raises `SourceScanError` (`source.py:84-90`) → `failed`/`unsupported` (`extraction.py:137-140`); `test_corrupt_pdf_not_reported_parsed`.
- C3: governor math `max(peak, rss-base)` (`scheduler.py:172`).
- E1: `page_retries` in `ParserConfig` (`config.py:51`), threaded via `extraction.py:162`.
- F1: document-wide median `body_med` shared (`native_pdf.py:151-160`, `loaders.py`); `legacy_kinds == engine_kinds` test.
- G1: dead code removed. G2: `_IMAGE_SLUGS` hoisted (`source.py:28`). G3: `Ledger.update_page` accumulates `prev+attempt` (`storage_pages.py:87`).

## Hard constraints re-confirmed
- `Extractor.extract(data, filename="", sha256=None, resume=False) -> ParseOutcome` signature + report keys unchanged; `expected_pages`/`actual_pages`/dead/failed/missing additive; only `parsed` when `actual_pages==expected_pages`.
- `DocumentBuilder.build` unchanged; `FilesystemStore` raw/dom/images untouched (pages/manifest additive).
- Router unchanged (ADR-011).
- Heavy engine built INSIDE worker (`_heavy_initializer`+`_run_heavy`); only serializable `PageWorkItem` crosses boundary.
- Reuse discipline intact: `_map_*`/`_recover_formula_text` imported; `enrich_scanned_pages` single-sourced.

## Residual note (non-blocking)
`periodic_recheck` downward resize sets `self._heavy_pool._max_workers` (private attr). Live workers are not force-killed, so the live count tightens only for future submissions. Satisfies "no upward mid-flight surge" + downward-only intent. Acceptable; flagged for the record.

## Conclusion
VERDICT: PASS. No remaining open issues.
