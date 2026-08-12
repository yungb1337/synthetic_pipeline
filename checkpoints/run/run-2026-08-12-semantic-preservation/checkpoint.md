# Checkpoint — run-2026-08-12-semantic-preservation

**Status:** COMPLETE (2026-08-12) · **Run:** `run-2026-08-12-semantic-preservation`
**Suite:** 179 passed / 1 skipped
**Trigger:** architectural investigation "Preservation of semantic document objects from
source representation into canonical DOM" (figures/equation/tables/metadata lost in transit).

---

## Objective
Faithfully carry every semantic object a source document *actually contains* into the canonical
DOM. The observed failures on `3548785.3548793.pdf`: **15 figures lost** (only Figure-1 caption
survived), **Equation 1 lost** (`diff = df_real.corr - df_synth.corr`), **table caption fused into
column headers** ("Adult Census Data (10K records).SD Metrics"), **metadata empty** (title/author).

## Root causes (distinguished — SOURCE EVIDENCE over inference)
| Symptom | Layer | Root cause |
|---|---|---|
| Figures dropped | loader→DOM | Docling emitted PictureItems but **`item.image is None`**: the production converter was built with the legacy `PipelineOptions` (no `generate_picture_images`), and `_map_image` silently returned on `None`. |
| Figures have no pixels even after flag | loader (converter construction) | **`artifacts_path=` is not a valid `DocumentConverter.__init__` kwarg** in Docling 2.118 → `DocumentConverter(**kwargs)` threw → fallback `DocumentConverter()` built the converter with **no pipeline options at all**. |
| Equation lost | loader | FormulaItem `.text=''` / `.latex=None` (transcription model produced nothing) and `_map_item` dropped all empty-text items. The equation **is** selectable text in the PDF. |
| Caption fused into header | loader | Docling's grid flags the full-width title row `column_header`; `export_to_dataframe()` builds a pandas **MultiIndex** over it and `str()` flattening fuses the title into every column name. |
| Metadata empty | loader | PDF info dict (via PyMuPDF) was never read into `RecoveredDocument` by either PDF loader. |
| Inverted bboxes | loader | Docling floating-item prov boxes are **BOTTOMLEFT** (y grows up); `_bbox` stored them raw, and the first normalization attempt mirrored the wrong edges. |

## What changed
- **`app/parser/loaders/docling_loader.py`**
  - `_build_converter` now prefers `PdfPipelineOptions` and only adds `artifacts_path` when the
    constructor signature declares it (the version-mismatch fallback previously built a converter
    with **no** pipeline options). `DOCLING_MODELS_PATH` env pin (set at import) still routes model
    caches on-prem.
  - `_make_pipeline_options` sets `generate_picture_images=True` + `images_scale=2.0`.
  - `_map_item` never drops a `formula` (typed block survives even with empty text), threads `doc`
    and the page height; `_map_image` handles `ImageRef.pil_image`/PIL, attaches `caption_text(doc)`,
    and **preserves a picture even when bytes are absent** (no silent loss).
  - `_map_table` reads the raw `grid` (not the dataframe): a leading full-width row spanning the
    table (`col_span >= ncols`) is a **title row → `caption`**; the real header is the last
    `column_header` row. Ragged body rows are padded, never truncated.
  - `_bbox` normalizes BOTTOMLEFT → PDF-point TOPLEFT (`new_t = page_h - old_t`, `new_b = page_h - old_b`).
  - `parse()` fills document metadata from the PDF info dict (shared `_pdfmeta.fitz_metadata`) and
    runs `_recover_formula_text` — the equation text is read back from the page layer at the
    formula's (now correct) bbox. Faithful: only `kind=="formula"` blocks, only when empty.
- **`app/parser/loaders/_pdfmeta.py`** (new) — shared fitz info-dict → `RecoveredDocument` metadata
  mapping (incl. readable PDF `D:` dates).
- **`app/parser/loaders/loaders.py`** — native `_pdf` now carries the same metadata.
- **`app/parser/parts.py`** / **`app/parser/dom/models.py`** / **`app/parser/dom/builder.py`** —
  additive `RecoveredTable.caption` / `Table.caption` (backward compatible; old DOMs keep working).

## Validation (source-vs-DOM, `3548785.3548793.pdf`, route=docling)
- **Figures:** 15/15 PictureItems preserved **with PNG pixels** (stored under
  `images/<doc_id>/<sha256>.png`, valid PNG magic); captions attached (Figure 1/3/4). No silent loss.
- **Equation:** `diff = df_real.corr - df_synth.corr (1)` now present as a `formula` block, bbox valid.
- **Tables:** Table 1 header `[Year, Name, Full name, Ref.]` with `caption="Table 1: List of used
  models"`; Table 4 `caption="Table 4: Airbnb Data Results"`. Caption never fused into headers.
- **Metadata:** title/author/subject/creator/producer/created/modified populated from the PDF info dict.
- Idempotency preserved: same source hash → same `document_id` `d-8984dbe978d3654b`.

## Known limitations (honest, not hidden)
1. **Figure 2 caption ref not attached** — Docling's caption-ref association fails for the page-8
   heatmap group; the caption text *is* preserved as a `caption` block on that page and every image
   is stored. Linking by proximity is deliberately NOT heuristic-ed in (the 6 sub-panels + 1 caption
   are ambiguous even for a human; the spec forbids fixture-driven heuristics). Downstream consumers
   can associate caption blocks ↔ images spatially.
2. **A page footer** ("IDEAS'22, …") is occasionally attached by Docling as a picture caption — a
   Docling ref-resolution artifact, preserved verbatim (never fabricated/edited).
3. **Table 4 header** is Docling's own group-header row (`['Airbnb Data']×3`) — faithful to the
   source grid, not a caption fusion.

## Tests
Added 8 tests to `tests/test_docling_loader.py` (title-row→caption, caption-wins-over-title,
ImageRef bytes + never-drop, empty-formula preserved, formula recovered from page layer, BOTTOMLEFT
bbox normalization, fitz metadata mapping, native-PDF metadata). Full suite **179 passed / 1
skipped** (skip is the expected Docling-installed guard).

## Next
Resume Module #4 (retrieval) or upstream figure-caption grouping (see Questions).
