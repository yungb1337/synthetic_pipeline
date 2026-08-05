# Gate 4 — Engineer Report (run-2026-08-04-docling)

## What was implemented (ADR-007: Docling gated layout/table backend)
- **`app/parser/config.py`** — new gating knobs `layout_backend` (`"native"|"docling"`, default
  `"native"`) and `docling_models_dir` (`"models/docling"`); both flow into provenance via snapshot.
- **`app/parser/parts.py`** — `RecoveredDocument` gains `reading_order_authoritative`,
  `docling_version`, `layout_model` (seam additions, backward compatible).
- **`app/parser/dom/models.py`** — `Provenance` gains optional `docling_version`, `layout_model`.
- **`app/parser/loaders/docling_loader.py`** (NEW) — lazy Docling engine mirroring `ocr.py`:
  compute-light pipeline (layout + tables, no OCR/code-formula), local model cache, per-process
  singleton converter, version-tolerant API construction, DoclingDocument → RecoveredDocument
  mapping (blocks/kind/bbox/page, tables via dataframe-or-cell-grid, images), authoritative reading
  order, "recovers nothing → return None" safety valve.
- **`app/parser/loaders/loaders.py`** — routing: `layout_backend=="docling"` sends PDFs/images
  through Docling when available, else falls through to the native path.
- **`app/parser/dom/builder.py`** — honors `reading_order_authoritative` (Docling order is final
  on that path; heuristic ROG retained for native); copies `docling_version`/`layout_model` into
  provenance.
- **`requirements-docling.txt`** (NEW) — optional heavy dependency (docling>=2.0), mirrors the
  `requirements-gpu.txt` pattern.
- **`tests/test_docling_loader.py`** (NEW) — default-backend, fallback-to-native, provenance/order,
  plus mapping-logic tests. Existing suite untouched.

## Test status
- `.venv/Scripts/python.exe -m pytest tests/ -q` → **green** (35 passed, 1 skipped; exit 0).
- The 1 skip is `test_docling_missing_falls_back_to_native`, correctly inactive now that Docling
  is installed (that path is covered on environments without Docling).
- **Real path VERIFIED** against installed `docling 2.118.0`.

## Verification notes (issues found & fixed during real-path testing)
1. **torch.compile / Triton** — Docling's layout model (RT-DETR) runs through torch.compile, which
   needs Triton (unavailable on Windows/Py3.14). Fixed by setting `TORCHDYNAMO_DISABLE=1` and
   `torch._dynamo.config.suppress_errors=True` in the loader → model runs eagerly.
2. **iterate_items shape** — docling-core yields `(item, level)` tuples in recent versions; the
   loader now unpacks both tuple and bare-item shapes.
3. **Model cache on-prem** — env vars (`DOCLING_MODELS_PATH`, `HF_HOME`) are set BEFORE the docling
   import so artifacts land under `models/docling/hf` (git-ignored), not the global HF cache.
4. **Fallback path** — if Docling is missing or recovers nothing, PDFs/images still parse natively
   (no crash), asserted by the skip-gated fallback test.

## Open items / risks (honest)
1. **`models_dir` singleton consistency** — the converter is built once from `default_config()`;
   a caller passing a custom `docling_models_dir` gets the env nudged (parse() sets
   `DOCLING_MODELS_PATH`), but the already-built converter's artifacts_path won't change. Default
   path is consistent; tighten if we ever need per-tenant model dirs.
2. **Images with `layout_backend=docling`** — Docling with no-OCR recovers no text from a bare
   image, so those fall back to the native RapidOCR path by design (safety valve). Documented.
3. **GPU nondeterminism** — Docling layout inference on GPU is not bit-for-bit stable across
   processes; identity is anchored on `sha256(source)`, and `docling_version`/`layout_model` are in
   provenance. If cross-process DOM equality ever matters, pin device + seed.
