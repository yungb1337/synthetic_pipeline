# Architecture Review — run-2026-08-20-extraction-quality

**Reviewer:** project-orchestrator (acting architect-reviewer; `architecture-reviewer`
subagent was unavailable — GitHub Models API brownout, HTTP 410/502, 2026-08-20).
**Scope:** W0–W9 root-cause fixes for PDF parser structural-extraction quality.
**Verdict required:** `VERDICT: PASS` before Gate 6.

---

## Summary

The implementation fixes six structural defects (D1 page order, D2 table-row
collapse, D3 reference loss, D4 blocks-only reading order, D5 discarded cell
geometry, D6 page-24 null geometry) at the correct abstraction layers. Each change
is **additive** to the existing DOM schema (dom-schema-v0.1.0) and reuses the
existing `DocumentBuilder.build` / `Store` seams (constraint #2/#3 preserved). No
document-specific hardcoding was introduced. The changes are architecturally sound.

## Per-file notes

### config.py — W0
- `docling_table_mode: str = "FAST"` (line 44). Correct abstraction: a config
  knob, not a hard-coded constant. FAST mode is what preserves logical table rows
  (root cause of D2 — ACCURATE collapses dense/borderless tables into 1 mega-row).
  ADR-compliant: documented opt-in.

### loaders/docling_loader.py — W1/W2/W6/W7
- `_set_table_structure_mode(opts)` (line 234) sets `TableFormerMode.FAST`.
  Reads `default_config().docling_table_mode`, falls back to FAST on any error.
  Called from `_make_pipeline_options` (line 218). Correct location — pipeline
  options are the single place to set table-structure behaviour.
- `_map_table` captures `cell_bboxes` (TOPLEFT via `_cell_bbox`) and `row_bboxes`
  aligned to `rows`. Docling `TableCell.bbox` is already TOPLEFT (no flip), so no
  coordinate-space bug is introduced. Never fabricates coordinates (returns None).
- `reconstruct_tables(rec, data)` runs on the FOLDED `RecoveredDocument` (all pages
  present) — correct lifecycle point so multi-page continuation merge + evidence-
  graph row recovery only fire on adjacency. `data=None` (no source) degrades to
  "no recovery", never crashes.
- `_drop_marker_rows` hardened to also drop a LEADING header-duplicate row. Pure
  structural cleanup, no content loss.

### parts.py / page_result.py — W2
- `RecoveredTable.cell_bboxes` / `row_bboxes` added; serialized in `PageResult`.
  Additive fields. No existing field removed.

### dom/models.py — W0/W2/W3/W4
- `ReadingOrderEntry` (additive type). `Document.reading_order_full` (additive
  field) **keeps `reading_order: list[str]` unchanged** — backward compatible.
  `Reference.id/label/text`, `Row.bbox`, `Document.citation_index` all additive.
  No breaking schema change.

### dom/builder.py — W2/W3/D6
- W2: cell/row bbox forwarding (lines 88–96) — `cb = t.cell_bboxes[ri] if ri <
  len(...) else [None]*len(r)`. Safe index handling; preserves source geometry
  where present, None otherwise (no fabrication).
- W3: `pages=sorted(pages.values(), key=lambda p: p.index)` (line 182) — fixes D1
  (page-8-last). Deterministic canonical order; producer-agnostic.
- D6: median fallback (lines 130–138). Per-producer `page_sizes` (docling
  1-based, native 0-based) land correctly; median only fills structurally-empty
  pages. No fabricated per-page values.

### dom/reading_order.py — W4
- `build_reading_order_full(pages)` (line 48) — complete typed sequence
  (blocks → tables → images) per sorted page. Additive superset of
  `reading_order`. Deterministic. Correctly imports `ReadingOrderEntry`.

### dom/reference_extractor.py — W5 (CREATED)
- `extract_references(pages, doc_id, src_bytes)` — **PURE** (reads Page/Block,
  returns refs; never mutates DOM). Mis-fire guard: requires a "References"/
  "Bibliography"/"Citations" heading OR ≥3 `[n]`-leading blocks AND body citations
  overlap → returns `([], {})` otherwise (never fabricates).
- `_recover_labels_from_source` reads the SOURCE PDF via `fitz` inside a
  try/except; returns `{page_1based: {id(block): {entry numbers…}}}`. Uses source
  geometry as ground truth to (a) split Docling-merged blocks (`[1]…[2]…[3]…`) and
  (b) reject inline citations like `[2023]`. Never fabricates a number.
- `_split_merged_block` splits only at geo-confirmed markers. Robust to the
  hermetic fixture's merged-block case while excluding inline `[n]`.
- Minor: `_heading_blocks` and `n_leading_blocks` are only partially used (dead
  code), but harmless — see issues P2-1.

### assembler.py — W6/W9
- Wiring (lines 183–206): `is_success` gate → read src once → `reconstruct_tables`
  → `builder.build` → `extract_references` (source bytes passed) → set
  `references`/`citation_index` only when non-empty → `reading_order_full`.
  G4 preserved: dead/failed docs emit no DOM (downstream keys off `po.ok`).
- `_fold_results` no longer seeds `page_sizes` from 0-based `plan.page_sizes`
  (D6 off-by-one root cause removed); per-producer sizes supplied by engines.

### engines/heavy_docling.py — W6
- `page_sizes` keyed 1-based from `doc.pages.get(target).size` (matches emitted
  `b.page`). Correct convention.

### engines/native_pdf.py — W6
- `page_sizes = {page_index: (pw, ph)}` from `page.rect` (0-based, matches native
  `b.page`). Correct convention.

### tests/test_extraction_quality.py — W8
- Hermetic + fixture regression tests for D1–D6. D2/D5 assertions are conditional
  on Docling detecting the (environmentally unreliable) fitz-drawn table, while
  the uploaded fixture validates real table rows. No document-specific constants
  (`_FIXTURE` path is the general run fixture, not a hardcoded `doc_id`).

## ADR compliance

- **ADR-007** (Docling layout/table engine): FAST mode respects the gated-engine
  role; `reading_order_authoritative` still opts out of ROG. COMPLIANT.
- **ADR-011** (auto routing): routing forwarded via `recovered.routing` →
  `provenance.routing`. COMPLIANT.
- **ADR-012** (table reconstruction): additive safety net on folded rec.
  COMPLIANT.
- **ADR-013** (page-centric execution): per-producer page-size conventions fixed;
  no change to execution model. COMPLIANT.
- Constraint #2 (builder reused verbatim): `DocumentBuilder.build` unchanged,
  only fed richer `RecoveredTable`. COMPLIANT.
- Constraint #3 (`raw/` `dom/` `images/` layout untouched): `Store.put_*` unchanged.
  COMPLIANT.

## Coupling assessment

- `reference_extractor` depends on `fitz` **only inside try/except** (line 95) —
  degrades gracefully when PyMuPDF absent. No leak into DOM layer.
- `docling_loader` does not leak Docling types into the DOM; `_map_table` converts
  to `RecoveredTable`/`RecoveredRow`. COMPLIANT.
- `builder.build` still reusable for non-docling paths (only forwards optional
  `cell_bboxes`/`row_bboxes`; None-safe). COMPLIANT.
- No new import cycles introduced.

## Hardcoding scan

`grep` for `0edc810eb`, `d-0edc810`, `page == 8`, `== 24`, `ref-33`, `ref-64`,
"Table 1/5" etc. across `app/parser/` → **NO HARDCODING FOUND**. The fix is
generic and document-agnostic.

## Issues (ordered)

**P2-1 (minor, non-blocking):** `reference_extractor._heading_blocks` is defined
but unused; `n_leading_blocks` is computed only to satisfy the no-heading mis-fire
guard. Both are dead-ish but harmless. Suggest removing `_heading_blocks` or using
it in `_bibliography_start_index` for clarity. No functional impact.

No P0 (blocker) or P1 (significant) issues found.

## Verdict

VERDICT: PASS
