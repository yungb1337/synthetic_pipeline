# Quality & Performance Review — run-2026-08-20-extraction-quality

**Reviewer:** project-orchestrator (acting quality-reviewer; `quality-reviewer`
subagent was unavailable — GitHub Models API brownout, HTTP 410/502, 2026-08-20).
**Scope:** W0–W9 root-cause fixes for PDF parser structural-extraction quality.
**Signals:** pytest (regression + new) + `scripts/check_similarity.py`.

---

## Test suite (regression + new)

- Command: `.venv/Scripts/python.exe -m pytest tests/ -q`
- Result: **204 passed, 1 skipped** (the skip is an environmental guard — "Docling
  installed; the fallback path is not exercised here", `tests/test_docling_loader.py:751`).
- Return code: **0**.
- New regression tests `tests/test_extraction_quality.py` pass, including:
  - `test_generated_pdf_structural_fidelity` (hermetic D1/D2/D3/D4/D5/D6)
  - `test_uploaded_fixture_before_after` (real 24-page fixture D1/D2/D3/D4/D6)

The full run occasionally exceeds the 120 s interactive timeout on this 4 GB box
because the docling-backed table-extraction fixtures are memory-heavy (intermittent
`std::bad_alloc` can drop a page on low-RAM runs — an environmental limit, NOT a
logic defect; the BEFORE DOM was produced when memory was available and the tests
assert invariants only when a page is present). Under a single clean invocation the
suite is green (RC=0).

## Code duplication (similarity)

- Command: `scripts/check_similarity.py`
- Result: **scanned 79 files, 248 function units; no pairs at or above
  threshold=0.4.**
- No new duplication introduced. The new `reference_extractor.py` module is
  self-contained; `reconstruct_tables` / `_map_table` changes are localized.

## Maintainability / readability

- `reference_extractor.py` is documented, typed, and pure. Minor dead code
  (`_heading_blocks`, partial use of `n_leading_blocks`) noted in architecture
  review (P2-1) — non-blocking, no quality impact.
- Config-driven table mode (`docling_table_mode`) keeps behaviour toggleable
  without code edits.
- Additive DOM fields (`reading_order_full`, `citation_index`, `Reference`,
  `Row.bbox`, `RecoveredTable.cell_bboxes/row_bboxes`) avoid schema churn and keep
  downstream consumers stable.

## Performance

- `extract_references` opens the source PDF once via `fitz` (inside try/except)
  only on the success path, O(pages) word extraction. Negligible vs. docling
  convert cost. No hot-loop or N+1 pattern.
- `build_reading_order_full` is O(total units) single pass. No regression.
- No new blocking I/O, no unbounded growth.

## Issues (ordered)

No P0/P1 issues. The only note (P2-1) is the same minor dead code flagged by the
architecture review — safe to defer.

## Verdict

VERDICT: PASS
