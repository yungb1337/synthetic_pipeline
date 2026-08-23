# Implementation Plan — Structural Extraction Fidelity (Gate 3)

**Run:** `run-2026-08-20-extraction-quality`
**Status:** `PLAN: READY` (revised post Gate-2 reviewer FAIL — P1/P2 resolved)
**Depends on:** `research.md` (Gate 1), `architecture.md` (Gate 2).

This plan is decomposed into independent, testable work units. Each maps to a defect (D1–D8) and a changed file from `architecture.md §3`. No document-specific hardcoding.

---

## W0 — Pre-flight: schema + config (foundation)
- `app/parser/config.py`:
  - Add `docling_table_mode: str = "FAST"` to `ParserConfig` (consumed by `docling_loader._make_pipeline_options`). Keep `dom_schema_version` as-is (no breaking change → no bump needed for this run; structural fields are additive).
- `app/parser/dom/models.py`:
  - Add `ReadingOrderEntry(BaseModel)`: `type: str` (`"block"|"table"|"image"`), `id: str`.
  - Extend `Reference` with `id: str = ""`, `label: str = ""`, `text: str = ""` (additive).
  - **Keep `Document.reading_order: list[str]` unchanged.** ADD `Document.reading_order_full: list[ReadingOrderEntry] = []` (additive; full typed sequence).
  - Add `Document.citation_index: dict[str, str] = {}` (additive; n → ref-id).
  - Add optional `bbox` to `Row` (row-level geometry, default None) — used by D5.
- Validation: existing pydantic models still construct; persisted v0.1.0 DOMs still validate (no type change).

## W1 — D2: Docling table-structure mode = FAST + reconstructor export (P0)
- `app/parser/loaders/docling_loader.py`:
  - In `_make_pipeline_options`, set `opts.table_structure_options.mode = TableFormerMode.FAST` (read from `ParserConfig.docling_table_mode`; map `"FAST"→TableFormerMode.FAST`, `"ACCURATE"→ACCURATE`; guard API drift — `TableFormerMode.AUTO` does NOT exist).
  - In `parse()` the sequence `_recover_formula_text → _evidence_reconstruct → normalize_tables` already runs; keep.
  - Export `reconstruct_tables(rec, data)` helper that runs `_evidence_reconstruct` (for collapsed tables) + `normalize_tables`, so the **assembler** can call it on the folded `rec` (NOT per-page heavy_docling).
- `app/parser/engines/heavy_docling.py`:
  - Do **NOT** call reconstruct/normalize here (per-page cannot merge multi-page continuations). Only set `PageResult.page_sizes` (1-based, see W6).
- Validation: re-run PDF; Tables 1/5/6 now have multiple rows matching source; no mega-row (FAST recovers rows at the Docling config layer).

## W2 — D5: cell/row geometry from Docling (P1)
- `app/parser/parts.py`:
  - Add `RecoveredCell` dataclass: `text: str`, `bbox: Optional[tuple]=None`.
  - Change `RecoveredTable.rows: list[list[RecoveredCell]]` (was `list[list[str]]`) — OR keep `rows: list[list[str]]` and add `cell_bboxes: list[list[Optional[tuple]]]`. **Decision:** keep `rows` as strings for adapter simplicity but add parallel `row_bboxes: list[list[Optional[tuple]]]` and `row_spans`/`col_spans` if needed. Simpler: extend `parts.RecoveredTable` with `cell_bboxes: list[list[Optional[tuple]]]` aligned to `rows`.
- `app/parser/loaders/docling_loader.py` `_map_table`:
  - When reading `grid[r][c]`, capture `getattr(c,"bbox",None)` (TOPLEFT already) → store into `cell_bboxes`.
- `app/parser/dom/builder.py`:
  - Build `Cell(text=..., bbox=_bbox(bbox))` using the aligned `cell_bboxes`.
- Validation: sampled cells (Table 5 r1c0, etc.) have non-null `bbox`.

## W3 — D1: canonical page order (P0)
- `app/parser/dom/builder.py` `build`:
  - `return Document(..., pages=sorted(pages.values(), key=lambda p: p.index), ...)`.
- Validation: page order == `1..24`.

## W4 — D4: typed reading order incl. tables/images (P1) — additive field, assembler stage
- `app/parser/dom/reading_order.py` (NEW helper or extend):
  - Add `build_reading_order_full(pages: list[Page]) -> list[ReadingOrderEntry]`: for each page in index order, append `{type:"block", id}` for its blocks (authoritative order), then `{type:"table", id}` for its tables, then `{type:"image", id}` for its images. Deterministic; follows Docling item order per page.
- `app/parser/assembler.py` (after `_fold_results` + `DocumentBuilder.build`):
  - `doc.reading_order_full = build_reading_order_full(doc.pages)`.
- `app/parser/dom/builder.py` `build`:
  - `reading_order` stays `list[str]` (block ids) — unchanged. NO change to `reading_order` type.
- `app/chunking/chunker.py`:
  - Unchanged (reads `reading_order` block ids).
- Validation: every table id + image id appears exactly once in `reading_order_full`; block coverage == `num_blocks`; `reading_order == [e.id for e in reading_order_full if e.type=="block"]` (back-compat check).

## W5 — D3: generic reference/bibliography extraction (P0) — assembler stage, mis-fire guard
- `app/parser/dom/reference_extractor.py` (NEW):
  - `extract_references(pages) -> (list[Reference], dict[str,str])`:
    1. Gather all block texts; find trailing bibliography region = the maximal suffix run of pages/blocks where lines match `^\s*\[(\d+)\]\s` or `^\s*(\d+)\.\s` with high density (≥60% of lines in a block, ≥3 such lines in a row). Structural, no literals.
    2. **Mis-fire guard:** require a "References"/"Bibliography" heading near the region OR cross-check that the detected entry numbers correspond to inline `[n]` markers present somewhere in the body; if neither signal present, return `([], {})` — never invent.
    3. Split into entries by the leading marker; for each entry assign `id=f"ref-{n}"`, `label=f"[{n}]"` (or `f"{n}."`), `text=entry_text`.
    4. Build `citation_index` **strictly from matched entry numbers** (never `range(1,max)`).
    5. Return `(refs, citation_index)`.
  - Robustness: if no bibliography region found, return `([], {})` (never invent).
- `app/parser/assembler.py` (after `DocumentBuilder.build`):
  - `refs, idx = extract_references(doc.pages)`; `doc.references = refs`; `doc.citation_index = idx`.
- Validation: `references` non-empty; entries have labels/ids; `citation_index` covers body `[n]`; no hardcoded numbers; trailing numbered "Limitations" list NOT invented as references.

## W6 — D6: page dimensions (P2) — fix 1-based vs 0-based key mismatch
- `app/parser/engines/heavy_docling.py`:
  - Populate `PageResult.page_sizes = {1-based page_no: (w,h)}` from Docling `result.document.pages` (use `page.page_no` which is 1-based, matching `block.page`).
- `app/parser/assembler.py` `_fold_results`:
  - Merge per-page `PageResult.page_sizes` into `rec.page_sizes` (1-based, to match `block.page`). Plan `page_sizes` from `source.py` are 0-based fitz indices — do NOT mix; builder uses `recovered.page_sizes[b.page]` (1-based) and reconciles with plan sizes by offset if needed (defensive).
- `app/parser/dom/builder.py`:
  - Look up `recovered.page_sizes.get(b.page)` (1-based). Fallback: if a page lacks dims but `metadata.page_count>0` and source uniform, use the median of known page sizes.
- Validation: all 24 pages have `612×792`; page 24 no longer null.

## W7 — D7: continuation cleanup (P2)
- `app/parser/loaders/docling_loader.py` `normalize_tables`:
  - Harden: drop a leading row that exactly repeats the canonical header (not just trailing markers).
  - Keep trailing-marker drop (last row only).
- Validation: page 12 Table 4 continuation has no "Continuation of Table 4" header-repeat and no "End of Table" marker row.

## W8 — Tests (regression + new)
- `tests/test_extraction_quality.py` (NEW): end-to-end on the fixture PDF (force docling route, CPU). Assert:
  - 24 pages, order `1..24`.
  - Tables 1/5/6 row counts match source (≥8, ≥11, ≥7).
  - Every table/image id in `reading_order_full` exactly once; block coverage == `num_blocks`; `reading_order == [e.id for e in reading_order_full if e.type=="block"]`.
  - `references` non-empty; `citation_index` populated; entries have `id`/`label`.
  - Sampled cells have non-null `bbox`.
  - All pages have non-null dims.
  - Token recall ≥0.90 (no-regression).
- Update `tests/test_docling_loader.py` for `TableFormerMode.FAST` and cell bbox; relax any `reading_order == block_count` equality assertion (now entries may exceed blocks) — see `test_docling_loader.py:772`.
- Update `tests/test_parser.py:58,72,85` and `tests/test_normalizer.py:99` reading-order assertions (block-subset, not exact list equality).
- `tests/test_chunker.py`: unchanged behavior (still reads `reading_order`); add a regression asserting `reading_order` contains all block ids.
- Run full suite: `.venv/Scripts/python.exe -m pytest tests/ -q` → green.

## W9 — D8/W1/D4/D3 convergence: assembler post-build enrichment (P0/P1/P2)
- `app/parser/assembler.py` `assemble` (or a new `_enrich` step after `DocumentBuilder.build`):
  1. `rec = reconstruct_tables(rec, data)` — runs `_evidence_reconstruct` (collapsed tables) + `normalize_tables` (multi-page continuation merge) on the **folded** `rec` (all pages present). This is the sole call site (not per-page).
  2. `doc = builder.build(rec)`.
  3. `refs, idx = extract_references(doc.pages)`; `doc.references = refs`; `doc.citation_index = idx`.
  4. `doc.reading_order_full = build_reading_order_full(doc.pages)`.
- This keeps `DocumentBuilder` a pure `RecoveredDocument → Document` mapping (principle #1).
- Validation: `assemble()` returns a `Document` with `reading_order_full` complete, `references` populated (if a bibliography exists), all tables reconstructed; no per-page reconstruction.

---

## Execution order (dependency-respecting)
W0 → W1 → W2 → W3 → W4 → W5 → W6 → W7 → W9 → W8.
W1–W7 + W9 are independently testable; W8 is the gate.

## Definition of Done (Gate 4)
- All P0/P1/P2 fixes implemented; no document-specific literals; no fabricated coords.
- Full pytest green (baseline 204/1 + new).
- The exact fixture PDF re-run yields: page order 1..24; Tables 1/5/6 structurally correct; references populated; reading order complete; cell bboxes present; page dims consistent; prose recall ≥0.90.

## BEFORE/AFTER (to be filled at Gate 4 close)
- Content fidelity: BEFORE 0.926 / AFTER ___
- Structural fidelity: BEFORE degraded / AFTER ___
- Table fidelity: BEFORE 3/8 tables collapsed / AFTER ___
- Reading-order fidelity: BEFORE blocks only / AFTER all units
- Reference fidelity: BEFORE 0 entries / AFTER ___
