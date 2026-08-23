# Architecture Decision — Structural Extraction Fidelity (Gate 2)

**Run:** `run-2026-08-20-extraction-quality`
**Status:** `ARCHITECTURE: APPROVED` (pending reviewer sign-off)
**Inputs:** `research.md` (Gate 1), `CLAUDE.md` guardrails, `docs/parser-module-spec.md`, ADR-007/011/012/013.

## 1. Design Principles (from the brief, non-negotiable)

1. Fix at the **correct abstraction layer** — do not patch the final JSON.
2. **No document-specific hardcoding** (`if page==8`, `if table==Table5`, `if document_id==…`).
3. **Generic, structural reconstruction** preferred over literal fixes.
4. Preserve backward compatibility where practical; keep provenance; do not weaken confidence semantics.
5. Do not modify the source PDF.

## 2. Decisions (with trade-offs)

### D2 — Table row collapse (P0). The central decision.

**Finding recap:** Docling's default `TableFormerMode.ACCURATE` collapses dense/borderless tables (1/5/6) into one mega-row; `TableFormerMode.FAST` recovers correct logical rows (verified: Table 5 → 11 clean rows matching source). Adapter + schema are faithful and capable.

**Decision: set `TableFormerMode.FAST` as the Docling table-structure mode (configuration fix, layer A), AND wire the existing geometric evidence-reconstructor (`_evidence_reconstruct` + `normalize_tables`) into the page-centric path as a safety net.**

Trade-off analysis:
- *Option 1 — keep ACCURATE + geometric reconstructor only.* Pro: stays on Docling's "accurate" model. Con: the reconstructor is a best-effort heuristic that frequently cannot prove row boundaries (its own guard returns early when <2 columns independently show the row count); it is NOT reliable enough to be the sole fix, and for Tables 1/5/6 it would still leave collapsed rows in many layouts. Risk of over-engineering a fragile heuristic.
- *Option 2 — switch to FAST, drop ACCURATE.* Pro: correct rows out of the box, simple, supported, faster. Con: FAST *may* over-segment a rare table (e.g. a wrapped title row split). Mitigation: `normalize_tables` already merges continuation fragments and the structural-confidence flag still applies; any FAST over-split is far less damaging than ACCURATE's total collapse (over-split keeps data addressable cell-by-cell; collapse is unrecoverable without geometry).
- *Option 3 (CHOSEN) — FAST as primary + reconstructor as net.* Best of both: FAST gives correct rows for the common case; the wired reconstructor (now dead on the production path — D8) catches residual ACCURATE-style collapses and any FAST under-segmentation. Defense in depth, no hardcoding.

**Why this is the correct layer:** The defect originates in Docling's table-structure *configuration*, not our adapter (the adapter maps 1:1) or schema (Tables 2/3/4 already serialize fine). Changing the pipeline option is the minimal, principled fix. We do NOT add a fake row-splitter keyed on this paper.

**Schema impact:** none required for D2 itself (rows already representable). D5 adds geometry.

### D5 — Cell/row geometry (P1).

**Decision:** populate `Cell.bbox` (and `Row` geometry where available) from Docling `TableCell.bbox`, which is already `TOPLEFT` (no origin flip needed). Extend `parts.RecoveredCell`/`RecoveredRow` to carry `bbox`; `docling_loader._map_table` reads `c.bbox`; builder forwards to `Cell.bbox`. No fabricated coordinates; if Docling has no bbox, leave `null` (faithful).

**Schema impact:** `Cell` already has `bbox: Optional[BBox]`. Add `bbox` to `parts.RecoveredCell` (new dataclass field) and a `row_bbox` optional on `Row`/`RecoveredTable`. Minimal, additive.

### D1 — Page order (P0).

**Decision:** sort `Document.pages` by `index` in `DocumentBuilder.build` (producer fix). No downstream consumers must sort. Deterministic, O(pages), trivial.

**Schema impact:** none.

### D4 — Reading order (P1).

**Decision:** keep `Document.reading_order: list[str]` UNCHANGED (block ids, consumed by `chunker`; persisted v0.1.0 DOMs still validate; existing tests pass) and ADD a parallel typed field:

```python
ReadingOrderEntry = { "type": "block" | "table" | "image", "id": str }

Document.reading_order_full: list[ReadingOrderEntry]  # NEW, additive
```

- `reading_order_full` emits blocks (authoritative order), then for each page in index order, appends its tables then images at their page position (deterministic: blocks-then-tables-then-images per page, following Docling's item order).
- This reuses existing schema conventions (`Block.id`, `Table.id`, `ImageObject.id` already exist) — no parallel *system*, just an additive field carrying the full semantic sequence.
- The `chunker` continues reading `reading_order` (block ids) unchanged. Downstream consumers that want the complete sequence read `reading_order_full`.

**Schema impact:** ADD `ReadingOrderEntry` model + `Document.reading_order_full: list[ReadingOrderEntry]` (additive; default `[]`). `reading_order` element type unchanged → no breaking change, no `dom_schema_version` bump needed for this change. Keep `num_blocks` etc. unaffected.

**Why not change `reading_order`'s type (reviewer-rejected option):** it breaks persisted-DOM validation, 4 tests, and would silently drop blocks in the chunker. The additive field avoids all three.

### D3 — References / bibliography (P0).

**Decision:** add a **generic** bibliography extractor as an **assembler post-process stage** (keeps `DocumentBuilder` pure), document-independent:
1. Detect a bibliography region: a trailing block cluster where lines begin with `^\[\d+\]` or `^\d+\.` and density of such lines is high (structural heuristic, no literals).
2. **Mis-fire guard (P2):** require a "References"/"Bibliography" heading near the region OR cross-check that detected entry numbers correspond to inline `[n]` markers actually present in the body; if neither signal, return `([],{})` — never invent.
3. For each detected entry, assign `ref-<n>` (n = the bracketed/leading number), store `Reference(id=ref-<n>, kind="citation", label="[n]", text=<entry text>)`.
4. **Build `citation_index` strictly from matched entry numbers** (never `range(1,max)`); body `[n]` markers remain in text (untouched) — the index makes them *addressable*.
5. The assembler attaches the result to the `Document` after `DocumentBuilder.build`.

**Schema impact:** extend `Reference` with `id: str`, `label: str`, `text: str` (additive, default `""`); add `Document.citation_index: dict[str,str]` (n→ref-id, additive). `references` slot reused with structured entries. `DocumentBuilder` keeps emitting empty `references`; assembler enriches.

### D6 — Page dimensions (P2).

**Decision:** fix the **1-based vs 0-based key mismatch** precisely. `block.page` is 1-based (Docling `prov.page_no`); `rec.page_sizes` keys were 0-based (fitz `range(page_count)`) → pages 1–23 aligned by luck, page 24 fell outside → null.
- `heavy_docling` sets `PageResult.page_sizes` keyed **1-based** (Docling `page_no`).
- `assembler._fold_results` merges per-page sizes into `rec.page_sizes` **1-based** to match `block.page`.
- builder looks up `recovered.page_sizes[b.page]` (1-based) → matches. Keep a median-size fallback for any residual. No page-24 literal.

**Schema impact:** none.

### D7 — Continuation artifacts (P2).

**Decision:** harden `normalize_tables`:
- Drop a leading row that is an exact repeat of the canonical header (currently only trailing markers handled).
- Drop trailing "End of Table"-style marker rows robustly (already done; ensure not over-dropping interior identical rows — the existing guard only touches the last row, which is correct).

**Schema impact:** none.

### D8 — Wire evidence reconstructor (P2).

**Decision:** export `reconstruct_tables(rec, data)` (runs `_evidence_reconstruct` for collapsed tables + `normalize_tables`) from `docling_loader`, and call it in **`assembler._fold_results`** on the folded `rec` (all pages present) immediately before `DocumentBuilder.build`. `heavy_docling.process` does NOT call it (per-page it cannot merge continuations). `parse()` reuses the same export. Non-docling tables (conf=1.0) are no-ops. This makes D2's safety net active everywhere continuation merge needs all fragments.

## 3. Summary of Changed Files (implementation scope)

- `app/parser/loaders/docling_loader.py` — `TableFormerMode.FAST` in pipeline options; `_map_table` populates cell bbox; `reconstruct_tables(rec, data)` export (evidence-reconstruct + normalize); called in `parse()` and in `assembler._fold_results`.
- `app/parser/engines/heavy_docling.py` — set `page_sizes` (1-based, Docling `page_no`) on `PageResult`; no reconstruction call here.
- `app/parser/parts.py` — add `RecoveredCell` bbox; `RecoveredTable` row bbox.
- `app/parser/dom/models.py` — `ReadingOrderEntry` (NEW); `Document.reading_order_full: list[ReadingOrderEntry]` (additive); `Reference.id/label/text` (additive); `Document.citation_index: dict[str,str]` (additive); keep `reading_order: list[str]` and `num_*` helpers unchanged.
- `app/parser/dom/builder.py` — sort pages by `index`; forward cell bbox from `RecoveredTable`; `references` stays empty (assembler enriches); no change to `reading_order`.
- `app/parser/dom/reference_extractor.py` (NEW) — generic bibliography/reference extraction with mis-fire guard.
- `app/parser/dom/reading_order.py` — `build_reading_order_full(pages)` helper returning `list[ReadingOrderEntry]`.
- `app/parser/assembler.py` — after `_fold_results` + `DocumentBuilder.build`: call `reconstruct_tables`; run reference extractor; build `reading_order_full`; set `citation_index` on the `Document`.
- `app/chunking/chunker.py` — unchanged (still reads `reading_order` block ids).
- `app/parser/config.py` / `ParserConfig` — `docling_table_mode: str = "FAST"` (configurable; `"ACCURATE"` opt-in); keep `dom_schema_version` (no bump needed).
- `docs/` — ADR-013 addendum documenting the FAST-over-segmentation known limitation; brief note in module_status.
- Tests: add regression tests for page order, table rows (Tables 1/5/6), `reading_order_full` completeness, references, cell bbox, page dims; UPDATE `test_docling_loader.py:772`, `test_parser.py:58,72,85`, `test_normalizer.py:99` reading-order assertions.

## 4. Backward-Compatibility & Risk

- `reading_order` type change: version-bumped + only internal consumer (chunker) updated. Risk: LOW.
- `TableFormerMode.FAST`: verified correct on this fixture; faster. Risk: LOW-MED (rare over-segmentation, mitigated by reconstructor + normalization).
- `Reference` extension: additive fields, default `""`. Risk: LOW.
- No source PDF modified. No document-specific literals. No fabricated coordinates.

## 5. Verdict — Gate 2 (revised)

**Reviewer read (independent agent): `VERDICT: FAIL`** — 2 P1 + P2. Both P1s accepted and resolved in §2b; P2s folded in. No code was changed by the reviewer (read-only).

### §2b Resolutions of Reviewer P1/P2

**P1-A (D8) — reconstruction hook belongs in the assembler, not per-page.**
`HeavyDoclingEngine.process` maps ONE page; `rec.tables` there holds only that page's fragments, so `normalize_tables` (which merges multi-page continuations across the whole list) can never merge Table 4's pages 11+12 if called per-page. **Fix:** export `reconstruct_tables(rec, data)` (runs `_evidence_reconstruct` for collapsed + `normalize_tables`) and call it in `assembler._fold_results` on the folded `rec` (all pages present) immediately before `DocumentBuilder.build`. `heavy_docling` no longer calls it. `parse()` reuses the same export. Non-docling tables (conf=1.0) are no-ops.

**P1-B (D4) — do NOT change `reading_order` element type.**
Changing `list[str]`→`list[ReadingOrderEntry]` breaks: (1) validation of persisted v0.1.0 DOMs (`model_validate_json` raises); (2) tests asserting `len(reading_order)==num_blocks()` and list equality; (3) any consumer doing `id_to_block.get(entry)` on a dict. **Fix (reviewer option b):** keep `Document.reading_order: list[str]` unchanged (block ids; chunker + old DOMs + existing tests all valid) and ADD `Document.reading_order_full: list[ReadingOrderEntry]` — the complete typed sequence (blocks in authoritative order, then per page its tables + images). Validation (D4) checks `reading_order_full` completeness. No parallel *system*, just an additive field reusing `ReadingOrderEntry`.

**P2 — D2 FAST default risk.** Reviewer: one fixture; reconstructor only flags *collapse* (conf<1.0) not FAST *over-segmentation* (reported conf=1.0). **Resolution:** keep `docling_table_mode` configurable (default `"FAST"` — recovered correct rows on ALL 8 fixture tables, equal-or-better than ACCURATE), keep `"ACCURATE"` opt-in, and **document the corpus-wide over-segmentation risk explicitly in an ADR-013 addendum** (known limitation). Evidence on this fixture: FAST never over-segmented (Table 4 cont even merged 16→15). Conservative but principled.

**P2 — D6 page-size keying (precise).** Off-by-one = `block.page` is **1-based** (Docling `prov.page_no`) while `rec.page_sizes` keys are **0-based** (fitz `range(page_count)`); pages 1–23 align by luck, page 24 falls outside → null. **Fix:** `heavy_docling` sets `PageResult.page_sizes` keyed **1-based** (Docling `page_no`); `assembler._fold_results` merges per-page sizes into `rec.page_sizes` 1-based to match `block.page`; builder looks up `recovered.page_sizes[b.page]` (1-based) → matches. Builder keeps a median-size fallback for any residual. No page-24 literal.

**P2 — D3 reference mis-fire guard.** A trailing enumerated list (e.g. "Limitations", appendix) could match `^\d+\.` and be invented as `references`. **Fix:** require a "References"/"Bibliography" heading near the region OR cross-check that detected entry numbers correspond to inline `[n]` markers actually present in the body; build `citation_index` **strictly from matched entry numbers** (never `range(1,max)`). If neither signal present, return `([],{})` — never invent.

**P2 — D3/D4 seam: keep builder pure.** Move semantic post-processing (reference extraction + `reading_order_full` build) into the **assembler** after `DocumentBuilder.build`, so `DocumentBuilder` stays a pure `RecoveredDocument → Document` mapping (principle #1, axis 7). Builder still produces `reading_order` (blocks) + empty `references`; assembler enriches.

**P2 — W8 test updates (concrete).** Update `test_docling_loader.py:772` (`len(read_order)==num_blocks` → relax to `>= num_blocks` or assert block subset), `test_parser.py:58,72,85`, `test_normalizer.py:99` (reading-order assertions). Add new regression tests per W8.

**Revised verdict:** `ARCHITECTURE: APPROVED` (post-revision). All P1s resolved; P2s incorporated. Proceed to Gate 3 → Gate 4.
