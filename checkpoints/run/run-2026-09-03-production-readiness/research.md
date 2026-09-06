# Production-Readiness Research — run-2026-09-03-production-readiness

## Context (different scenario from previous run)
The `run-2026-09-03-pymupdf4llm-eval` folder (architecture-tradeoffs.md) was deleted. The previous `run-2026-08-20-extraction-quality` run is complete. This is a new production-readiness remediation run.

## Verdict line (required by protocol)
`RESEARCH: COMPLETE` — all 5 failure points reproduced with line evidence. Recommendation: implement all 5 (user selected this). Decision owned by Chief Architect / Gate 2.

## What's reproduced (5 of 8 checks fail)

| ID | Defect | Source file / lines | Root cause |
|---|---|---|---|
| F-03 | Torn ledger → audit trail erased silently | `storage_pages.py:57-60`, `63-70`, `74-76`, `89-92` | Non-atomic `.tmp` recovery fails; `load_plan` silently returns `None` on corruption; updates become no-ops |
| F-04 | Quadratic ledger rewrite | `storage_pages.py:72-90` (`update_page`) | `load_plan` reads full JSON → mutates one key → `write_plan` serializes full `plan.json` per page |
| F-05 | Native path O(n²) | `native_pdf.py:162-171` | Per-page `extract_page` re-scans all N pages to compute document-level median font size |
| F-06 | PDF reopened per page | `native_pdf.py:146`, `184-214`, `152-182` | `fitz.open()` called 21x for 20-page doc; engine instance rebuilt per page or `.doc_cache` not reused |
| F-07 | `page_exists` treats FAILED as done | `storage_pages.py:49-50`, `extraction.py:262-268` (safety net uses `get_page` correctly but planner relies on `page_exists`) | `page_exists()` checks file presence, not status; planner `done.add(pidx)` uses it as success proxy |

Not reproduced: F-01, F-02, F-08 (out of scope for this 5-point mandate).

## Fix-layer mapping
- F-03 → durability (atomic write + load_plan corruption handling)
- F-04 → storage format (per-page small files or append-only, avoid full-ledger rewrite)
- F-05 → engine (native_pdf): compute median once per doc, pass into per-page helper
- F-06 → engine (native_pdf): open once per document, reuse handle
- F-07 → storage / extraction: `page_exists()` must distinguish `OK` vs `FAILED`/`DEAD`; planner must use `get_page()` + status check

## What must NOT break (ADR-013 hard gates)
- Page = durable processing unit (`PageResult` persisted atomically)
- Document = orchestration unit (`DocumentValidator` gate `assembled_set == expected_set`)
- Idempotent resume (`resume=True`) must load OK pages into `results`, never dead-letter them
- `PageStatus` semantics (`OK`, `FAILED`, `DEAD`, `PENDING`) preserved; no silent `None` returns
- Config (`ParserConfig`) flows to engine; native path uses `body_med` parameter in `_native_page_from_doc`

## Source of evidence
Every line number was read directly from the code (`cat`-equivalent reads). Nothing was invented. The harness runs in temp directories and never writes to the repo.

`RESEARCH: COMPLETE`
