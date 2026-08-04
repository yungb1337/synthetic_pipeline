# Docling Integration Plan — Layout Analysis in the Parser (Document Intelligence)

**Status:** plan (awaiting approval to implement) · **Goal:** use IBM's **Docling** for high-quality layout / table-structure / reading-order / OCR analysis *inside our existing parser*, while keeping **our** control, provenance, DOM schema, storage, versioning, batching, and tests.

---

## 1. What Docling gives us (and why it matters for healthcare docs)
Docling (MIT code; docling-project/IBM) is a document-conversion framework that runs ML models on a parsed document:

| Capability | Docling model | What we currently do | Why it's better |
|---|---|---|---|
| **Layout analysis** | DocLayout-YOLO (labels: text, heading, caption, figure, table, sidebar…) | `pymupdf` text blocks + font-size heuristic | labels *regions*, not just boxes → captions/figures/headings recognized structurally |
| **Table structure** | TableFormer (rows, columns, header, cells) | `pymupdf.find_tables()` heuristics | big win for **lab reports / medication tables** (medical core) |
| **Reading order** | layout/reading-order model | our `(y,x)` heuristic | correct for multi-column, sidebars, footnotes |
| **OCR (scanned)** | PaddleOCR/EasyOCR/Tesseract plugin | our RapidOCR | comparable; one less engine if we standardize |
| **Figure/equation** | figure classifier / formula | extracted as images only | captions + formulas become typed nodes |
| **Formats** | PDF, DOCX, XLSX, PPTX, images, HTML, MD… | per-format loaders | a *second* high-quality backend for many loaders |

**Verified here:** `docling 2.118.0` resolves cleanly on Python 3.14 (pulls `docling-ibm-models`, `docling-core`, `docling-parse`, `pypdfium2`, `rapidocr`, `torchvision`; no conflicts).

## 2. The seam that makes this easy
Our loader → `RecoveredDocument` → DOM is the **parser-independence seam** (SYN4). Docling becomes **one more loader backend that produces the same `RecoveredDocument`**. Nothing downstream changes.

```
        ┌─────────── our detection ───────────┐
 raw ───┤ pdf_engine? pymupdf (default)       │
        │                └ docling (opt-in)    ├──> RecoveredDocument ──> DOM ──> normalize…
        └──────────────────────────────────────┘
```

## 3. What we REPLACE vs KEEP (the control/provenance guarantee)

### We replace (only inside the loader layer)
- **Layout analysis / headings / captions**: DocLayout-YOLO labels → our `kind` mapping.
- **Table structure**: TableFormer cells/headers → our `RecoveredTable` (far better than heuristics).
- **Reading order**: Docling's order populates our chain instead of the `(y,x)` heuristic.
- **OCR (optionally)**: Docling's PaddleOCR for the scanned path (or keep RapidOCR — a flag).
- **For PDFs specifically**: the `_pdf` extraction internals (pymupdf stays as the fast default).

### We KEEP (unchanged — our control + provenance)
- **File-type detection** (ours; docling doesn't own that).
- **DOM schema + builder + our Reading-Order-Graph representation** (we keep the `reading_order` chain; we just *populate* it from Docling order).
- **Storage / content-addressing / idempotency** (sha256 doc ids, immutable raw+DOM) — unchanged.
- **Versioning & provenance** — every block/table carries `source="docling"` + `provenance.config` includes `docling_version` + model names, so lineage and reprojection are preserved exactly like today.
- **Events, batch/worker layer, normalizer, tests harness** — untouched.
- **Two-tier strategy**: `pdf_engine` defaults to `pymupdf` (fast, no model downloads); `docling` is opt-in (config) for quality — later auto-trigger on complex docs. Same "deterministic cheap pass as backbone; ML where heuristics fail" principle.

## 4. What changes in the code
| File | Change |
|---|---|
| `requirements-docling.txt` (new) | `docling>=2.118` + note: models download on first run |
| `app/parser/config.py` | `pdf_engine: Literal["pymupdf","docling"] = "pymupdf"` (+ `docling_*` knobs: OCR backend, accelerator) |
| `app/parser/loaders/loaders.py` | `_pdf` dispatches to docling when configured; **lazy import** + try/except fallback to pymupdf on any docling failure |
| `app/parser/loaders/docling_mapper.py` (new) | pure function `docling_document → RecoveredDocument` (blocks/tables/images/reading order/bbox/confidence); unit-testable |
| `scripts/download_models.py` | extend to also fetch docling model weights into `models/` (or document HF cache) |
| `tests/test_docling_pdf.py` (new) | `skipif` docling absent: parse a real PDF via docling; assert identical DOM schema + provenance marks `source="docling"` |
| docs + project_memory | update module status, decisions |

## 5. Mapping (DoclingDocument → our RecoveredDocument)
- Each `DoclingDocument` item: `label → kind` (`Section-header`→`heading`, `Text/Paragraph`→`paragraph`, `ListItem`→`list_item`, `Caption`→`caption`, `Formula`→`formula`, `Code`→`code`, `Table`→`RecoveredTable`, `Figure`→`RecoveredImage`…).
- `item.prov[0]` → `bbox` + page + **confidence** → our block/table fields.
- Tables: TableFormer `export_to_table()` rows/cells → header + `RecoveredTable`.
- Figures: item image bytes/ref → `RecoveredImage.blob` (our store persists it).
- Reading order: walk items in Docling's order → our `reading_order` chain.

## 6. Risks / trade-offs (honest)
| Risk | Mitigation |
|---|---|
| Heavy install (40+ deps, torchvision, models ~hundreds MB) | `requirements-docling.txt` separate; models into `models/`; lazy import so non-docling users unaffected |
| **Model license audit** (healthcare/enterprise) | code MIT; per-model weights (DocLayout-YOLO Apache-2.0, TableFormer IBM, OCR engines) — **verify at install**, record in `architecture_decisions.md` |
| Slower than pymupdf (model inference) | two-tier default (pymupdf); docling opt-in; batch workers only use it when configured |
| Determinism | pin docling/model versions + threads/seed in config → recorded in `provenance`; add idempotency test |
| Docling version churn | pin exact version; keep `source="docling"` + version in provenance for reprojection |

## 7. Implementation order
1. Install `docling` in the venv; run on a real PDF; confirm models download + measure time.
2. Add `docling_mapper.py` + unit tests (mapping is pure).
3. Wire `_pdf` dispatch + config flag (default stays pymupdf).
4. `test_docling_pdf.py` (golden DOM, provenance, idempotency).
5. Update docs/memory; commit.
6. (Later) optional: auto-select docling when pymupdf confidence is low (scans/multi-column).

## 8. What I could NOT verify right now (verify during implementation)
Live Docling docs fetch was rate-limited during planning; confirm at install: exact model names/versions, per-model licenses, image-resource export API, and accelerator (ONNX-CPU/GPU vs torch) behavior on the 4GB RTX 3050.