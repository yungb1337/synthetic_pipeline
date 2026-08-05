# Gate 1 — Research: Docling as layout + table engine (run-2026-08-04-docling)

`RESEARCH: COMPLETE`

> Note: this environment is offline (web search/fetch returned nothing). Findings below are
> labeled by certainty; version-specific claims are marked **Research (verify at install time)**
> and the architecture must not hard-depend on unverified API details — the loader must pin to
> whatever version installs and degrade gracefully if Docling is absent.

## 1. Summary recommendation
Adopt Docling as the **layout + table-structure backend for the PDF/scanned path only**, wrapped
behind our existing `RecoveredDocument` seam as a new loader, gated by config so the cheap native
path remains the default for everything that does not require layout analysis. Keep the heuristic
reading-order and `find_tables` code in the codebase but **off by default for the PDF path when
`layout_backend="docling"`**; do not run Docling per-document unless the document actually needs
layout (or the caller opts in). This honors "Docling present but triggers where layout analysis is
required" and "keep computation expense low."

## 2. Evidence per question

**Q1. Docling current state (Aug 2026)** — **Fact** (general), **Research** (exact version).
- Fact: Docling is IBM's open-source document understanding toolkit; pip-installable as `docling`; MIT-style license; Python ≥ 3.10 classically (verify). It depends on `docling-core` (the `DoclingDocument` model), `docling-ibm-models`, `transformers`, `torch`/`onnxruntime`, `vllm`-optional for acceleration. It is a **heavy** dependency tree.
- Research: exact latest release + required Python must be confirmed by `pip index versions docling` / install at implementation time. Architecture must not assume a specific version.

**Q2. API surface & stage toggling** — **Fact**.
- Fact: The primary API is `DocumentConverter` (with `PipelineOptions`) producing a `DoclingDocument`. Pipeline stages historically: layout model (DocLayNet), table-structure model, reading-order (tables + classic), code/formula detection, optional OCR bridge (Tesseract/EasyOCR), assembly. PipelineOptions allow disabling sub-stages (e.g., `TableStructureModelOptions.enabled`, `CodeFormulaModelOptions.enabled`, `OcrOptions`), so you can run **layout + tables only** and skip OCR/code-formula — this is the compute-cutting lever.
- Inference: exact option names differ across versions; the loader must be written against the installed version's API and guarded by try/except + feature sniffing, following the lazy-loading pattern already used for OCR.

**Q3. Model footprint & offline** — **Fact** (general), **Research** (exact paths/sizes).
- Fact: model artifacts download from HuggingFace on first use; Docling supports a local model cache dir (`DOCLING_MODELS_PATH` env or artifacts dir option). All models are local once cached → fits the on-prem "no data leaves machine" posture.
- Fact: Docling is designed for GPU but runs on CPU via onnxruntime; CPU is substantially slower. Per-document cost is dominated by layout + table models.
- Inference: memory footprint is in the hundreds-of-MB to low-GB range per loaded model — relevant for `app/processing/executor.py` worker pool sizing.

**Q4. Docling → our seam** — **Fact** (model shape is stable across versions).
- `DoclingDocument` is a directed graph of `Item`s: `TextItem`, `SectionHeaderItem`, `TableItem`, `PictureItem`, `CodeItem`, `FormulaItem`, `ListItem`, `CaptionItem`. Items carry `prov` (provenance with bbox in page coordinates + page number), and there is a top-down iteration (`.iterate_items()`) that yields items in **reading order**.
- `TableItem` holds an `OOTCell`-based grid (`TableData`) with `table_cells` exposing row/col spans, header cells, and body cells — enough to reconstruct our `RecoveredTable(header, rows)`.
- Mapping: for each item in reading order → `RecoveredBlock(kind, text, bbox, page, source="docling")`; `SectionHeaderItem`→kind `heading`; `PictureItem`→`RecoveredImage` (store bytes via our store); `TableItem`→`RecoveredTable`; keep our reading-order chain = Docling's iterate order (drop the naive `reading_order.py` heuristic for the PDF path).

**Q5. Determinism / idempotency** — **Inference**, partly **Research**.
- Fact: Docling output for identical bytes + identical model versions is effectively deterministic on the same backend; GPU floating-point can introduce small nondeterminism.
- Inference: our `sha256(source)` identity holds because identity is over *source bytes*, not the DOM. To keep re-parse stability and auditability, record in `Provenance`: docling package version + model artifact identifiers. Add `docling_version` / `layout_model` to provenance.

**Q6. Gating strategy** — **Recommendation** (three options compared).
| Option | Cost | Accuracy | Complexity |
|---|---|---|---|
| (a) Explicit config flag `layout_backend=docling` per parse/corpus | lowest | user-controlled | trivial |
| (b) Heuristic pre-screen (text density / column detect) to auto-route | low | medium, but reintroduces heuristics we're deleting | medium |
| (c) Always Docling for PDF+images | highest | best | trivial |
Recommendation: **(a) explicit config flag, defaulting to the cheap path**, plus (c)-lite: Docling auto-engages for **image/scanned** documents where layout analysis is genuinely required (no native text). Document-level `layout_backend` override in `ParserConfig`; recorded in provenance. This satisfies "triggers where layout analysis is required" without a fragile auto-detector.

**Q7. Alternatives** (≥3 real) — **Fact**.
| Option | Table fidelity | Multi-column ROG | CPU cost | Offline/license | Determinism | Seam fit |
|---|---|---|---|---|---|---|
| PyMuPDF + heuristics (current) | weak (bordered only) | weak | low | yes | high | — |
| pdfplumber | medium | no | low | yes | high | loader swap |
| `unstructured` | low | low | low | mixed | low | poor |
| `deepdoctection` | medium | medium | med-high | yes | medium | heavy |
| **Docling** | **high** | **high** | high (GPU-recommended) | yes | medium-high | good (loader swap) |

**Q8. Risks**
- Install weight + first-run model download in an on-prem/offline hospital env → pre-cache models to `models/` via the existing download-script pattern; gate on presence.
- CPU-only perf → keep Docling opt-in; do not make it the default hot path.
- API drift across Docling versions → lazy import + feature-sniff + pin, mirroring `ocr.py`.
- GPU nondeterminism vs our idempotency → provenance records docling + model versions; identity stays on source sha256.
- Worker memory in `executor.py` → load Docling once per process (lazy singleton), never per doc.

## 3. Open decisions for architecture gate
1. Trigger model: (a) config flag default-cheap + auto-engage for scans. Confirm.
2. Whether to **delete** `reading_order.py` heuristic or keep it for non-PDF/fallback. (Recommend: keep as fallback for formats without Docling coverage; Docling path overrides it.)
3. Provenance additions (docling version, model ids).
4. requirements: new dependency line(s) — decide if `docling` is optional/extra (recommend optional extra `docling` so base install stays lean).
