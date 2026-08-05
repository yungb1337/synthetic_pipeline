# Gate 3 — Implementation Plan (run-2026-08-04-docling)

`PLAN: READY`

## Goal
Docling present, gated to where layout analysis is required. On the Docling path: heuristics
(reading_order.py naive ROG + PyMuPDF `find_tables`) are replaced by Docling's layout/tables/ROG.
Native path stays the cheap default. DOM schema, events, storage unchanged. Tests green.

## Files & changes

### 1. `app/parser/config.py` — gating knobs (versioned, snapshotted into provenance)
- `layout_backend: str = "native"` — `"native" | "docling"`.
- `docling_models_dir: str = "models/docling"` — local model cache (on-prem).
- Both go into `snapshot()` automatically (they're plain fields).

### 2. `app/parser/parts.py` — seam additions (minimal)
- `RecoveredDocument.reading_order_authoritative: bool = False`
  → Docling path sets True so the builder trusts Docling's order instead of re-running the heuristic.
- `RecoveredDocument.docling_version: Optional[str] = None`
- `RecoveredDocument.layout_model: Optional[str] = None`

### 3. `app/parser/loaders/docling_loader.py` — NEW (mirrors `ocr.py` lazy engine)
- `engine_available() -> bool` — lazy `from docling.document_converter import DocumentConverter`
  (+ `docling_core`), singleton; returns False if import fails → caller falls back to native.
- `engine_name() -> str|None` — docling package version.
- `DoclingBackend` class holding one lazily-built converter (loaded once per process, not per doc).
- `parse(data: bytes) -> RecoveredDocument | None`:
  - Builds `PipelineOptions` disabling OCR + code/formula (layout + tables only = compute-light;
    API sniffed defensively).
  - `DocumentConverter(...).convert(input=BytesIO(data), ...)` → `DoclingDocument`.
  - `iterate_items()` in reading order → map each item:
    - `SectionHeaderItem` → kind `heading`; `TextItem` → `paragraph`; `ListItem` → `list_item`;
      `CodeItem` → `code`; `FormulaItem` → `formula`; `CaptionItem` → `caption`.
    - `TableItem` → `RecoveredTable`: build header + rows from `table_cells` (header-role cells →
      header; body cells grouped by row span).
    - `PictureItem` → `RecoveredImage` (page, bbox, mime, bytes from `item.image` via BytesIO).
    - bbox/page from `item.prov[0]` (page_no + bbox). All wrapped in try/except per item — a bad
      item degrades, never crashes.
  - Sets `reading_order_authoritative=True`, `docling_version`, `layout_model`.
  - Empty result → return `None` so caller falls back to native.

### 4. `app/parser/loaders/loaders.py` — routing (native stays default)
- In `Loaders.load()`: for `pdf` (and image slugs `png/jpg/gif/tiff`), if
  `self.config.layout_backend == "docling"`:
  `rec = docling_loader.parse(data)`; if `rec is not None` → return it; else fall through to native.
- Native `_pdf`/`_image` paths unchanged (still the cheap default; `find_tables`/ROG remain only here).

### 5. `app/parser/dom/builder.py` — honor authoritative order + provenance
- If `recovered.reading_order_authoritative`: `ordered = recovered.blocks` (Docling order is final);
  else existing `reading_order.recover_reading_order(recovered.blocks)`.
- Copy `recovered.docling_version` / `layout_model` into `Provenance` (new optional fields).

### 6. `app/parser/dom/models.py` — Provenance additions (optional, backward-compatible)
- `docling_version: Optional[str] = None`
- `layout_model: Optional[str] = None`

### 7. `requirements-docling.txt` — NEW (mirrors `requirements-gpu.txt` pattern; base stays lean)
- `docling` (+ pinned `docling-core`); install with `pip install -r requirements-docling.txt`.
- README gets a short "optional Docling backend" section.

### 8. Tests — `tests/test_docling_loader.py` (NEW) + keep suite green
- `pytest.importorskip("docling")` at module top → docling-path tests skip when not installed
  (offline/CI safe), matching the OCR lazy pattern.
- Test: `layout_backend="docling"` + installed → PDF parses, provenance.docling_version set,
  reading order chain == block count.
- Test: docling NOT installed + `layout_backend="docling"` → falls back to native, still `ok`,
  no crash (deterministic degrade).
- Test: `layout_backend="native"` (default) unchanged — existing suite already covers.
- Existing `tests/test_parser.py` untouched and must stay green.

## Order
config → parts → models → docling_loader → loaders routing → builder → requirements → tests → run suite.

## Definition of done for this gate
- `.venv/Scripts/python.exe -m pytest tests/ -q` green.
- Docling path verified when `docling` is installed (try `pip install -r requirements-docling.txt`;
  if the environment is offline, note it and rely on the skip-gated tests).
