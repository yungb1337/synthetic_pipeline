# Quality Review — Gate 6 RE-REVIEW, run-2026-08-19-page-centric

**Reviewer:** quality-reviewer (read-only pass)
**Prior verdict:** FAIL → re-review after fix loop R1.

## Verdict
VERDICT: PASS

All 5 MAJOR (M1–M5), 3 MODERATE (Mo1–Mo3), and 5 MINOR (Mi1–Mi5) from the prior FAIL are
genuinely closed — each confirmed in code, not just claimed. No new correctness or duplication
risk introduced. Key gate-6 regression tests 10/10 green; similarity script reports zero
problematic pairs.

## Verification per prior finding (all CLOSED)
- **M1** `assembler.py:53-57` `assembled_page_set` requires `status in (OK,PARTIAL) AND content_present`; `classify` (68-71) mirrors it. PARTIAL/empty stub cannot count as assembled → dead-letters. Matches §3.13.
- **M2** `source.py:81-90` raises `SourceScanError` on fitz-open failure / `page_count<=0`; `extraction.py:137-140` → `status="failed"` (never `parsed`). `test_corrupt_pdf_not_reported_parsed` green.
- **M3** `assembler.py:235-241` routes `band=="docling"` to `HeavyDoclingEngine`, not native; `scheduler.py:308-317` sends docling → heavy pool only. `test_assembler_retry_docling_stays_docling` green.
- **M4** `heavy_docling.py:31` substantive `process`; tests `test_heavy_docling_failure_returns_failed`, `test_heavy_docling_empty_stub_returns_partial_failed_no_content` (content_present False), `test_heavy_run_worker_convert_none_returns_failed` present + passing.
- **M5** `planner.py:108-153` `plan(resume=True)` reads ledger, skips OK+present, reschedules FAILED/DEAD attempt+1, never clobbers; `executor.py:113` `resume=True`. Resume tests green.
- **Mo1** `config.py:51` `page_retries:int=2`; `extraction.py:162` passes `max_retries=self.config.page_retries`. No hardcoded `2`.
- **Mo2** `scheduler.py:265-271` `__init__` derives `heavy_concurrency=derive_heavy_concurrency(F=None)` → 1; `measure_footprint` only inside `_heavy_initializer` (worker, line 59). Orchestrator never loads engine for native/non-docling runs. `periodic_recheck` wired (332-346, downward-only).
- **Mo3** `native_pdf.py:151-160` + `loaders/loaders.py:172-184` both use document-wide median `_body_med` → shared `_native_page_from_doc`. Heading-parity tests green.
- **Mi1** dead code removed (grep confirms: no `read_source`/`is_image_slug`/unused `asdict`; `scheduler.py` imports only `as_completed`+pools).
- **Mi2** `_IMAGE_SLUGS` single def `source.py:28`, imported by `planner.py:18`.
- **Mi3** `scheduler.py:172` `max(peak, rss-base)` — no double-count.
- **Mi4** `storage_pages.py:87` attempts accumulate `(prev...) + (attempt or 1)`.
- **Mi5** `scripts/check_similarity.py` now reports NO pairs ≥0.4 (no new duplication).

## Sanity checks (all PASS)
- Bounded memory: `run_plan` persists each `PageResult` via `put_page`+`update_page` on completion (`scheduler.py:363-372`); assembly folds one doc in-memory. No whole-corpus buffering.
- No silent drop/double-count: `PageWorkItem` 1:1 from `expected_page_set`; `DocumentValidator` hard gate; miss → failed/dead with explicit report; `update_page` accumulates, never clobbers.
- Exception containment: `_run_heavy`/`_run_native`/`_collect` wrap worker failures into `FAILED` `PageResult`s; exception never escapes orchestrator.
- `ImageEngine` attaches blob as `RecoveredImage` → `content_present=True` (F1 image fix).

## Issue list
None open.

## Conclusion
VERDICT: PASS.
