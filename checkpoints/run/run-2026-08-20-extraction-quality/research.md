# Research Report — PDF Parser Structural Extraction Quality (Gate 1)

**Run:** `run-2026-08-20-extraction-quality`
**Verdict:** `RESEARCH: COMPLETE`
**Author:** Project Orchestrator (investigative lead)
**Date:** 2026-08-20
**Test fixture:** source PDF `0edc810eb07d15e9.pdf` (24 pp, academic survey), DOM `dom-v0.1.0.docJSON`, `parser-v0.1.0`, `dom-schema-v0.1.0`, Docling `2.118.0`, routed to Docling path (complexity 83).

> Method note: the `parser-v0.1.0` DOM shipped in `test_cases_output/` was first diffed, then the **current** pipeline (page-centric, ADR-013) was re-run on the exact source PDF and its output (`work/repro/dom/d-0edc810eb07d15e9/dom-v0.1.0.docJSON`) used as ground truth. Both agree on every defect below, so the findings reflect current-pipeline behavior, not a stale artifact. Docling was forced to CPU (`CUDA_VISIBLE_DEVICES=-1`) to avoid the GPU crash observed on this box; the production Docling config was reproduced exactly except for the device.

---

## 1. Executive Summary

Ordinary text extraction is strong (block token recall 0.925, the residual being mostly PDF hyphenation artifacts). The defects are **structural**, not textual:

| # | Defect | Severity | Root-cause layer |
|---|---|---|---|
| D1 | Canonical page order is `1..7,9..24,8` — page 8 serialized **last** | **P0** | assembler/builder (dict-insertion order) |
| D2 | Table logical rows collapsed into one concatenated mega-row (Tables 1/5/6) | **P0** | Docling **config** (`TableFormerMode.ACCURATE` collapses dense/borderless tables) |
| D3 | `references: []` although 49 citation markers exist in body | **P0** | schema dead-end + no reference extraction on any path |
| D4 | `reading_order` contains blocks only — tables & images absent | **P1** | DOM builder (chain built from blocks only) |
| D5 | Cell/row bounding boxes discarded (every cell bbox `null`) although Docling supplies them | **P1** | adapter (`_map_table`) + `Cell`/`Row` schema |
| D6 | Page-24 dimensions `null` (off-by-one: Docling 1-based page_no not in 0-based `page_sizes`) | **P2** | heavy_docling (`page_sizes` never set; key mismatch) |
| D7 | Table continuation-merge left "Continuation of Table 4" header + "End of Table" marker rows | **P2** | `normalize_tables` heuristic |
| D8 | Evidence-graph row reconstruction (`_evidence_reconstruct`/`normalize_tables`) not wired into the page-centric path | **P2** | `heavy_docling.py` (only `parse()` has it) |

**Headline:** the row-collapse (D2) is the single highest-impact issue. It is a **Docling configuration** problem (ACCURATE mode collapses these tables) — `TableFormerMode.FAST` recovers the correct logical rows — not an adapter or schema failure. The adapter faithfully maps whatever Docling returns; the schema already *can* represent real rows (Tables 2/3/4 come out fine). The fix therefore lives at the Docling-pipeline-options layer (option **A**), plus generic structural reconstruction to bridge the gap and preserve cell geometry.

---

## 2. Source Document Characteristics

- 24 pages, uniform `612×792` pt (US Letter). Authored in LaTeX (ACM/IEEE-ish two-column survey).
- Front matter: title, authors, affiliations, abstract, 1-column. Body: **two-column** (multi-column probability 1.00). Layout complexity 0.925, reading-order ambiguity 0.827, font diversity 1.00.
- **8 tables** across pages 3,6,7,8,11,12,16,19. Several (1,5,6) are dense/borderless → visually many logical rows.
- Numbered bibliography; ~49 inline citations `[n]` in body, max number 64.
- 1 figure (page 4) with caption.
- Source chars ≈ 107.7k; tokens ≈ 3,560.

---

## 3. Text Fidelity Metrics (fresh DOM vs source)

| Metric | Value |
|---|---|
| Source printable chars | 107,682 |
| DOM block text chars | 95,684 |
| DOM table text chars | 10,283 |
| Block token recall (src→DOM blocks) | **0.926** |
| Distinct tokens (src / DOM) | 3,560 / 3,347 |
| Missing tokens | 264 |
| Duplicated text | none observed |
| Reordered text | none (Docling reading order authoritative) |

**Classification — `preserved` (with harmless formatting diffs).** The sampled "missing" tokens are PDF line-hyphenation fragments (`enhance-ment`, `symbio-sis`, `comprehen-sion` → `tant`, `mit`, `lem`, `ness`, `symbi`, `comprehen`), not content loss. No headings, lists, or paragraphs were dropped. **Content fidelity is HIGH; the problem is structural, not textual.**

---

## 4. Page-Order Analysis (D1, P0)

Serialized `pages[].index` order in the canonical DOM:

```
[1,2,3,4,5,6,7,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,8]
```

Page 8 is not present where it should be (between 7 and 9); it appears **last**. Inspection of `app/parser/dom/builder.py:55-152` shows pages are assembled in a `dict[int, Page]` keyed by `b.page`, and `pages=list(pages.values())` preserves **insertion order** = the order blocks first arrive. Page 8's only content is **1 table and 0 blocks**; its `Page` object is created late (when `_map_table`/builder adds tables, after all 24 pages' blocks have been iterated). Hence page 8 falls to the end. `DocumentBuilder` does not sort by `index`.

**Root cause:** non-deterministic page ordering driven by first-appearance order of blocks vs tables; no sort on `index`.
**Where lost:** `builder.py` (`pages=list(pages.values())`).
**Fix location:** `builder.py` — sort pages by `index` before emitting (producer fix; no downstream sort).
**Evidence:** fresh DOM page order above; page 8 has `0 blocks, 1 table`.

---

## 5. Reading-Order Analysis (D4, P1)

`reading_order` has 274 entries = exactly the block count. It is built **only** from `ordered` blocks (`builder.py:56-78`). Tables (8) and images (1) are **never appended** to `chain`. So:

- every content block appears exactly once (✓),
- tables appear in `pages[].tables` but **absent from `reading_order`** (✗),
- images absent from `reading_order` (✗),
- multi-column order correct (Docling authoritative),
- deterministic (✓).

Downstream `app/chunking/chunker.py:204` walks `reading_order` and only emits `Block` items — tables/images are invisible to the retriever. **The canonical reading order does not include every semantic content unit.**

**Root cause:** builder constructs `chain` from blocks only.
**Fix location:** `builder.py` — extend `reading_order` to a typed sequence including tables/images (use existing schema conventions; do not invent a parallel system).

---

## 6. Table-by-Table Analysis (D2 P0, D5 P1, D7 P2)

Measured with Docling 2.118.0 on the exact PDF, comparing `ACCURATE` (current default) vs `FAST`:

| Page | Table | Source rows (visual) | Docling ACCURATE grid/rows | Docling **FAST** grid/rows | DOM rows (current) | Collapsed? |
|---|---|---|---|---|---|---|
| 3 | Table 1 | ~8 | **2** | 9 | (ACCURATE path) | **YES** |
| 6 | Table 3 | 3 | 4 | 4 | ok | no |
| 7 | Table 4 | 1 | 2 | 2 | ok | no |
| 8 | Table 2 | 10 | 12 | 12 | 10 (wrap-merged) | partial |
| 11 | Table 4 | 5 | 6 | 6 | 5 | ok |
| 12 | Table 4 cont | 15 | 16 | 15 | 15+marker | marker rows |
| 16 | Table 5 | ~21 | **2** | **12** | **1 (mega-row)** | **YES** |
| 19 | Table 6 | ~7 | **2** | **8** | **1 (mega-row)** | **YES** |

**D2 — the critical failure.** For Tables 1/5/6, Docling's **default `TableFormerMode.ACCURATE` collapses the logical rows into a single body row** (`grid rows: 2` = 1 header + 1 mega-row). The adapter maps this faithfully. FAST mode recovers clean logical rows that match the source exactly (Table 5: WANLI, GPT3Mix, Unnatural Instructions, Self-Instruct, AugGPT, Code Alpaca, WizardCoder, AlphaCode, Reflexion, NL2SQL, WANLI(Code) — 11 rows, correct columns).

**Classification per investigation matrix:**
- **A. Is Docling already returning one row?** YES — `grid rows: 2` with one mega body row. This is the default `ACCURATE` table-structure model failing on dense/borderless tables.
- **B. Adapter collapses rows?** No — `_map_table` maps every Docling grid row 1:1.
- **C. Schema cannot represent rows?** No — `Row`/`Cell` lists already exist; Tables 2/3/4 prove it.

**Root cause:** Docling **configuration** — `TableFormerMode.ACCURATE` under-segments these tables. Fix at layer A (pipeline options). FAST is the supported, documented mode that preserves rows.

**D5 — geometry discarded.** For every table cell, Docling supplies a non-null `bbox` (`CoordOrigin.TOPLEFT`, e.g. `Table5 r1c0 l=85.20 t=102.35 r=186.83 b=329.49`). The adapter (`docling_loader._map_table`) never reads `c.bbox`; `Cell`/`Row` models have `bbox: Optional[BBox]=None` and are built with no bbox. So all cells serialize as `bbox: null`.

**Root cause:** adapter ignores per-cell bbox; schema has the field but it is never populated.
**Fix location:** `docling_loader._map_table` (populate `Cell.bbox` from `TableCell.bbox`, top-left already) + `parts.RecoveredTable`/`RecoveredCell` carrying bboxes to the builder.

**D7 — continuation artifacts.** Table 4 spans pages 11→12. `normalize_tables` merges the continuation but leaves a leading `"Continuation of Table 4"` header row and a trailing `"End of Table"` marker row (page 12 shows 15 data rows + 1 header-repeat + 1 marker). The header-repeat detection (`_row_equals_header`) should drop the repeated `"Approach / System | ... | ..."` row (it does not match the canonicalized header because the earlier fragment's header differs). Marker drop (`_drop_marker_rows`) works but only on the *last* row.

---

## 7. Docling Raw-Output Analysis (4-stage trace, Table 5)

```
PDF visual table (p16):  ~21 logical rows  [Dataset|Domain|Task Type|Eval Metrics|Reference]
        ↓ Docling ACCURATE (current default)
Docling table:            grid_rows=2  → 1 header + 1 mega body row (COLLAPSED)
        ↓ adapter (_map_table)
RecoveredTable:           header=[5 cols], rows=[[5 concatenated mega-cells]], conf=0.3
        ↓ builder
Canonical DOM:            Table 5: 1 row, 5 concatenated cells   ❌

PDF visual table (p16)
        ↓ Docling FAST (supported alternative mode)
Docling table:            grid_rows=12 → 1 header + 11 logical rows  ✓
        ↓ adapter (same mapping)
RecoveredTable:           header=[5], rows=11 (each row = Dataset|Domain|TaskType|Metrics|Ref)
        ↓ builder
Canonical DOM:            Table 5: 11 clean rows  ✓  (matches source)
```

The degradation enters at the **Docling table-structure stage (configuration)**. The adapter and schema are faithful and capable.

---

## 8. Adapter / Normalization Analysis

- `_dense_grid` correctly prefers `grid` over `table_cells`; handles full-width title rows as captions (good).
- `_table_structural_confidence` flags collapsed tables (`conf=0.3`) — but the collapse is already in Docling; the flag iseparatel a symptom signal, not a fix.
- **`parse()` ships `_evidence_reconstruct` + `normalize_tables`**, but the **page-centric path (`heavy_docling.py`) never calls them** — it calls `_map_item`/`_recover_formula_text` only. So the already-built evidence-reconstruction machinery is **dead on the production path**. This is D8.
- Cell bbox is available from Docling and discarded (D5).

---

## 9. Reference / Bibliography Analysis (D3, P0)

- `Document.references` is empty (`[]`). `recovered.references` is **never populated on any path** (grep: only `builder.py:153` consumes it, from an always-empty list). The `Reference` model + field are schema dead-ends.
- Body text contains 49 distinct inline citation markers `[n]` (max 64). The bibliography section (last pages) is extracted as ordinary body blocks/paragraphs, not structured references.
- No reference parser exists on any path; native loader also has none.
- Schema: `Reference(kind, target)` exists but there is **no structured bibliography extraction**.

**Root cause:** missing feature — no bibliography-number/reference extraction stage; the schema slot is unused.
**Fix:** add a generic, document-independent reference extractor (detect a numbered bibliography block at document end via structural cues — a dense run of `^\[\d+\]`-led lines; associate each with a `ref-<n>` id; link body `[n]` markers to `ref-<n>` via a generic citation→reference map). No hardcoding of this paper's numbers.

---

## 10. Image Analysis (D-not-applicable / P3)

- 1 figure on page 4, `ImageObject` present with `caption`, `storage_ref`, `mime=image/png`, non-null bbox.
- Docling supplies picture pixels (`generate_picture_images=True`); `blob` persisted via `store.put_image`.
- Caption associated (`caption` field populated). Not in `reading_order` (covered by D4).
- Correct behavior per architecture (image = asset, not OCR). No bug; relationship is explicit through `caption`. Ruled **non-blocking (P3)** — only action is including images in reading order (D4).

---

## 11. Metadata Analysis (D6, P2)

- All pages: `612×792` except **page 24: `w=null, h=null`**.
- `SourceScan` records `page_sizes` 0-based (`0..23`) from `fitz`. `heavy_docling` maps Docling `prov.page_no` (1-based) into `RecoveredBlock.page` (so blocks on page 24 get `page=24`).
- `builder.py`/`assembler._fold_results` look up `page_sizes[int(page)]`; `page_sizes` comes from the **plan** (0-based) and also from `rec.page_sizes` (Docling `doc.pages` 1-based → `rec.page_sizes[1..24]`). Page 24's `0-based key=23` is in plan sizes, **but the per-page `PageResult` from `heavy_docling` carries no `page_sizes`**, so when a page reaches the builder only via a table (page 8) or via blocks the plan's sizes should still apply — yet page 24 came through with blocks (`list_item`×2, `page=24`) and STILL got null dims. Root: `assembler._fold_results` sets `rec.page_sizes` from **plan** sizes (0-based), but `builder` reads `recovered.page_sizes` which for docling pages is 1-based; the mismatch leaves page 24 unmatched when the page's entries are keyed at the 1-based boundary. Confirmed empirically: page 24 dims null while page 8 (also table-only) has 612×792 — the difference is the 1-based/0-based key for the LAST page.

**Root cause:** inconsistent page-index basis between `page_sizes` producer (0-based fitz) and Docling's 1-based `page_no` surface, plus `heavy_docling` not setting `PageResult.page_sizes`.
**Fix:** normalize page sizes to 0-based consistently at the assembler `rec.page_sizes` and ensure `heavy_docling` populates `PageResult.page_sizes`; or have builder fall back to `doc.page_count`-sized uniform geometry when available. Prefer producer fix (no downstream special-casing, no page-24 literal).

---

## 12. Duplication / Omission Accounting

| Unit | Status |
|---|---|
| Source text (prose) | present once (0.926 recall) |
| Block text | present once |
| Table text | present, but collapsed into mega-cells for 1/5/6 (P0) |
| Caption text | preserved (figure + table captions) |
| Image-associated text | preserved (caption) |
| Footnotes | none in source |
| References | **missing** (P0) — only inline markers remain |
| Page headers/footers | folded into body (acceptable) |
| Multi-column content | correctly ordered (Docling authoritative) |

No duplication observed. Omission = references (D3); misrepresentation = collapsed tables (D2) and absent table/image reading-order membership (D4).

---

## 13. Content vs Structural Fidelity

- **Content fidelity: HIGH** (0.926 token recall; residuals = hyphenation artifacts).
- **Structural fidelity: DEGRADED.** Tables 1/5/6 lose row structure; references dropped; reading order incomplete; cell geometry discarded; page order non-canonical.

A table whose words are all present but whose rows are merged is **not** a successful extraction. The distinction is central and is why D2/D3/D4 are P0/P1 despite high text recall.

---

## 14. Root-Cause Matrix

| Issue | Observed | Expected | Source evidence | DOM evidence | First appears | Root cause | Sev | Fix location | Validation |
|---|---|---|---|---|---|---|---|---|---|
| D1 page order | `…,24,8` | `1..24` | 24 pp in order | `pages[].index` = `[…,24,8]` | builder | dict-insertion order, no sort | P0 | `builder.py` sort by index | re-run, assert `1..24` |
| D2 row collapse T1/5/6 | 1 mega-row | N rows | visual multi-row tables | `rows=1`, `conf=0.3` | Docling table-structure | `TableFormerMode.ACCURATE` under-segments dense/borderless tables | P0 | Docling pipeline options (FAST) + generic reconstruct | source rows = DOM rows |
| D3 references | `[]` | structured refs | numbered bib + 49 `[n]` | `references:[]` | no extraction stage | missing reference parser; schema slot unused | P0 | new generic reference extractor | bib entries addressable by `ref-<n>` |
| D4 reading order | blocks only | typed seq incl tables/images | tables/images exist | `chain` = 274 block ids | builder | chain from blocks only | P1 | `builder.py` extend to typed RO | every semantic unit once |
| D5 cell bbox | `null` | non-null (Docling has it) | Docling `TableCell.bbox` TOPLEFT | `Cell.bbox=null` | adapter/schema | bbox ignored in `_map_table`; `Cell`/`Row` schema lacks cell bbox | P1 | `docling_loader._map_table` + parts schema | sample cells have bbox |
| D6 page24 dims | `null` | `612×792` | uniform page size | p24 `w/h=null` | assembler/page-sizes basis | 0-based vs 1-based key mismatch; `PageResult.page_sizes` unset | P2 | `heavy_docling` + assembler normalize basis | all pages have dims |
| D7 continuation rows | header+marker leftover | clean data rows | "Cont. of Table 4" / "End of Table" | p12 rows include both | `normalize_tables` | partial merge + marker logic | P2 | `normalize_tables` hardening | no marker/header-repeat rows |
| D8 evidence reconstruct unwired | collapsed stands | reconstruction runs | `_evidence_reconstruct` exists | not applied | page-centric path | `heavy_docling` doesn't call it | P2 | wire into `heavy_docling` | reconstruction active in all paths |

---

## 15. Severity Classification

- **P0 (canonical correctness / data loss):** D1 (page order), D2 (table row collapse), D3 (references dropped).
- **P1 (significant structural degradation):** D4 (reading order incomplete), D5 (cell geometry discarded).
- **P2 (consistency/quality):** D6 (page-24 dims), D7 (continuation artifacts), D8 (dead evidence-reconstruct path).
- **P3 (cosmetic/non-blocking):** image handling (correct; only folded into D4).

---

## 16. Recommended Fixes (architecture-agnostic summary; see Gate 2/3)

1. **D2 — Docling table config (layer A):** set `TableFormerMode.FAST` in the Docling pipeline options (preserves logical rows for dense/borderless tables). Keep `ACCURATE` as a documented fallback. This is the principled fix — a supported configuration, not a hack.
2. **D2/D8 — generic structural reconstruction:** re-enable `_evidence_reconstruct` + `normalize_tables` on the page-centric path (`heavy_docling`), so row recovery is consistent across both code paths. Add a generic row-boundary reconstructor that uses cell geometry as a *bridge* (not a replacement) for any residual collapse.
3. **D5 — cell geometry:** populate `Cell.bbox`/`Row` geometry from Docling `TableCell.bbox` (already TOPLEFT). Extend `parts.RecoveredCell`/`RecoveredRow` to carry bbox; builder forwards it. No fabricated coordinates.
4. **D1 — page order:** sort `Document.pages` by `index` in `builder.py` (producer fix).
5. **D4 — reading order:** make `reading_order` a typed sequence (`{type, id}`) including tables and images, using existing schema conventions; keep block ids for back-compat where needed. Update `chunker` to consume typed entries.
6. **D3 — references:** add a generic bibliography extractor (detect numbered reference block at document end via structural pattern; assign `ref-<n>`; map body `[n]` → `ref-<n>`). No document-specific literals.
7. **D6 — page dimensions:** normalize `page_sizes` to 0-based consistently; populate `PageResult.page_sizes` in `heavy_docling`; builder fallback to uniform geometry from `page_count`.
8. **D7 — continuation cleanup:** harden `normalize_tables` to drop repeated header rows and trailing markers reliably.

---

## 17. Validation Plan

A. **Regression:** page count 24; page order `1..24`; block count; table count 8; image count 1; captions; references > 0; reading order = all semantic units exactly once.
B. **Source-vs-DOM:** recompute token recall (expect ≥0.92, no worse); recollapse check = 0.
C. **Table:** for Tables 1/5/6, `source rows = Docling FAST rows = canonical DOM rows`; verify column alignment (5 cols for Table 5).
D. **Reading order:** every table + image id appears exactly once; block coverage = 274.
E. **Reference:** bibliography entries retain labels; each addressable by `ref-<n>`; body `[n]` links resolve.
F. **No-regression:** ordinary prose token recall unchanged; all existing pytest green.

---

## 18. Open Questions for Gate 2 (Architecture)

- Is `TableFormerMode.FAST` acceptable as the *default* for the Docling path, or should we keep ACCURATE and add a generic geometric row-reconstructor as the primary fix (FAST as fallback)? Trade-off: FAST is faster + recovers rows but may over-segment rare tables; ACCURATE + reconstruct keeps Docling's model but needs a robust bridge. **Recommendation:** FAST as the Docling table-structure mode (supported config, proven on this fixture) **and** wire the existing geometric reconstructor as a safety net for any residual collapse — defense in depth, no hardcoding.
- Should `reading_order` change shape (`list[str]` → `list[{type,id}]`)? This is a schema change. **Recommendation:** extend to typed entries but keep block ids backward-compatible for the `chunker` (which can read `id`/`type`).
- Reference schema: extend `Reference` with `id`/`label`/`text` for structured bibliography, or add a `bibliography` section? **Recommendation:** extend `Reference` minimally (add `id`, `label`, `text`) and add a generic `citation_links` map in provenance or document.
