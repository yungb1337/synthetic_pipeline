# Gate 6 — Quality & Performance Review (run-2026-08-04-docling)

`VERDICT: PASS`

## Checks
- **Tests** — `.venv/Scripts/python.exe -m pytest tests/ -q` → **35 passed, 1 skipped** (skip =
  real-Docling path, gated on `docling` not installed). New tests cover: default backend, fallback
  to native, provenance + authoritative reading order, and a **docling-independent white-box mapping
  test** so the DoclingDocument→RecoveredDocument logic is exercised even offline.
- **Duplication signal** — `scripts/check_similarity.py`: one pair at 0.50
  (`ocr.py::engine_available` ~ `docling_loader.py::engine_available`). This is the **intentional,
  documented lazy-engine pattern** (ADR-007 explicitly says "mirror ocr.py"). Acceptable; recorded
  for the org.
- **Maintainability** — `docling_loader.py` is defensive (version-drift try/except) and commented;
  ~280 lines for a version-tolerant third-party bridge is proportionate. Mapping helpers are small
  and pure.
- **Efficiency / performance** —
  - Native path pays nothing: Docling is never imported unless `layout_backend=="docling"`.
  - Converter (and models) loaded once per process; reused across docs — no per-doc cold start.
  - Temp-file write per doc is I/O-negligible vs ML inference.
- **Correctness** — routing, mapping (blocks/tables/images), provenance, and authoritative-order
  chain all asserted by tests. Image persistence reuses the existing content-addressed store path.

## Findings (minor, non-blocking)
- **Q1 — RESOLVED** Real-Docling path is **verified** against installed `docling 2.118.0`
  (test_docling_path_records_provenance_and_order passes; 506M of model artifacts cached on-prem
  under `models/docling/hf`, git-ignored). Two real environment issues were found and fixed during
  verification: (a) torch.compile needs Triton (absent on Windows/Py3.14) → loader sets
  `TORCHDYNAMO_DISABLE` + `suppress_errors` so the layout model runs eagerly; (b) docling-core
  `iterate_items()` yields `(item, level)` tuples → loader unpacks both shapes.
- **Q2 (minor)** `_layout_model_name` often returns `None` (converter internals vary by version);
  harmless — `docling_version` is the reliable provenance signal.

No blocking issues. Code is testable, maintainable, and performant on the default path.
