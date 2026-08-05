# Gate 5 — Architecture Review (run-2026-08-04-docling)

`VERDICT: PASS`

## Reviewed against
- `project_memory/architecture_decisions.md` → ADR-007
- `checkpoints/run/run-2026-08-04-docling/architecture.md`
- Changed files: `config.py`, `parts.py`, `dom/models.py`, `dom/builder.py`,
  `loaders/docling_loader.py` (new), `loaders/loaders.py`, `requirements-docling.txt`,
  `tests/test_docling_loader.py`

## Checks (all verified by reading the code, not trust)
1. **Seam intact** — Docling maps into `RecoveredDocument`; the DOM/harness/events/storage layer is
   untouched. ✅ `docling_loader.py` returns `RecoveredDocument`; `extraction.py` unchanged.
2. **Gating honored** — `layout_backend` defaults to `"native"`; Docling routes only PDFs/images and
   only when the flag is set ([loaders.py](app/parser/loaders/loaders.py#L85)). ✅
3. **Compute-light** — `do_ocr=False`, `do_code_formula=False`; converter is a per-process lazy
   singleton; the native path never imports Docling. ✅
4. **Heuristics replaced on the Docling path** — `reading_order_authoritative` bypasses the heuristic
   ROG ([builder.py](app/parser/dom/builder.py#L50)); tables come from Docling's `TableItem`, not
   `find_tables`. The heuristics remain only as the native (cheap) default, per ADR-007. ✅
5. **On-prem posture** — models cached under `models/docling/`, `DOCLING_MODELS_PATH` set. ✅
6. **Determinism/idempotency** — identity stays on `sha256(source)`; `Provenance` records
   `docling_version` + `layout_model`. ✅
7. **Degrade-safety** — engine absent or empty result ⇒ `None` ⇒ native fallback; never raises. ✅
8. **DOM schema backward-compatible** — two optional `Provenance` fields default to `None`. ✅

## Findings (minor, non-blocking — recorded for the checkpoint)
- **F1 (minor)** `_models_dir()` in `docling_loader.py` reads `default_config()` rather than the
  config instance used at routing time; the singleton converter is built once, so a caller passing a
  custom `docling_models_dir` won't re-set the engine's env. Default path is consistent; tightens if
  we ever need per-tenant model dirs.
- **F2 (minor, by design)** bare images routed to Docling with no-OCR recover no text, so they fall
  back to the native RapidOCR path. Documented; not a defect.

No architecture violations. The change matches ADR-007 exactly.
