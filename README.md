# Synthetic Data Factory — Parser Module (Extraction → DOM)

The first module of an enterprise **Synthetic Data Factory** whose product is **trust**: transforming a hospital's proprietary documents into a canonical, parser-independent Document Object Model (DOM) that everything downstream (normalization, chunking, knowledge graph, generation, validation) consumes.

## Status
**Module #1 — Parser (Extraction → DOM): implemented, tested, green.**
**Module #2 — Normalizer (DOM → clean DOM): implemented, tested, green.**
**Batch/Scale layer + OCR/embedding batching: implemented, tested (24 tests green).**
Each process is an independent package (modular monolith).

## Layout (modular monolith)
```
app/parser/       Module #1 — Parser
  config.py       versioned, immutable per-parse config
  detection.py    magic bytes → container probe → content sniff (extension last)
  parts.py        format-agnostic "recovered" intermediates (loader→DOM seam)
  dom/            models.py · reading_order.py (in-memory ROG) · builder.py
  loaders/        one loader per format → RecoveredDocument
  ocr.py          on-prem RapidOCR (lazy, never blocks other formats)
  storage.py      Store protocol + FilesystemStore (swap to S3/DB later)
  events.py       outbound events (document.parsed.v1 / failed)
  extraction.py   Detect → Load → Build → Store → Emit (public entry)
  cli.py          `python -m app.parser.cli --in <file|dir> --out <store>`

app/normalizer/   # Module #2 — Text Normalization
  rules.py        pure, idempotent rules (strip_controls·nfkc·dehyphenate·ws·typography)
  normalizer.py   DOM → normalized DOM + provenance report
  cli.py          `python -m app.normalizer.cli --dom <parsed.json> --out <normalized.json>`

app/processing/   # Batch/scale execution layer (thousands→millions of docs)
  corpus.py       parallel hashing + persistent {sha256} manifest (incremental/durable)
  executor.py     worker pool: parse→normalize, retries, BatchReport, crash-safe manifest
  cli.py          `python -m app.processing.cli --in <corpus> --out <store> [--concurrency N]`

app/embedding/    # Batching-capable embedding seam (real model drops in later)
  embedder.py     `Embedder` protocol (list-in → vectors-out, never one-at-a-time)
  dummy.py        deterministic placeholder `DummyEmbedder`
  runner.py       `batch_embed()` (model-sized batches, shape-guard) + embed_document_blocks
```

## Run
```bash
python -m venv .venv
# activate (Windows: .venv\Scripts\activate)
pip install -r requirements.txt

# end-to-end:
python -m app.parser.cli --in <file_or_dir> --out parser_out

# tests:
python -m pytest
```

## Supported inputs (Extraction → DOM)
PDF (text + layout + headings + tables + images), DOCX, XLSX, CSV/TSV, JSON, XML, HTML, Markdown, plain text, and images / scanned (on-prem OCR).

## Local models & GPU
Two local, open-source model types are used; **no document ever leaves the machine**. Model weights live in the repo under `models/` (git-ignored; large binaries).

- **OCR — already local.** RapidOCR-onnxruntime bundles PaddleOCR detection+recognition models and runs on the on-prem `onnxruntime` (CPU by default). Batched for many pages; engine loads only when a doc needs OCR.
- **Embeddings — `BAAI/bge-m3` (1024-dim, multilingual) on your GPU.** `app/embedding/` uses `sentence-transformers` via PyTorch **CUDA** onto the RTX 3050 (fp16 to fit 4GB), with automatic CPU fallback. `factory.default_embedder()` picks whichever is available; `DummyEmbedder` is only the deterministic fallback for tests/CI/machines without torch.
  - Install/update the model into `models/`:
    `PYTHONPATH=. python scripts/download_models.py`  (→ `models/bge-m3`)
  - Installed for this machine (verified): `torch-2.13.0+cu126`, `sentence-transformers-5.6.1` (see `requirements-gpu.txt`).
  - Verify: `PYTHONPATH=. python scripts/check_embedder.py`

## Optional Docling backend (ADR-007) — layout + tables, gated
Docling is **present but triggers only where layout analysis is required**, keeping computation
expense low. On the Docling path it replaces the heuristic reading order and PyMuPDF `find_tables`
with learned layout/table-structure/reading-order models; the cheap native path remains the default
for everything else.

```bash
pip install -r requirements-docling.txt     # heavy: pulls torch/transformers + onnx models
# then opt in per parse/corpus:
#   ParserConfig(layout_backend="docling")  # "native" (default) | "docling"
```
- Models cache locally under `models/docling/` (on-prem; nothing leaves the machine).
- If Docling is missing or recovers nothing, PDFs/images fall back to the native path (never crash).
- Provenance records `docling_version` / `layout_model`; reading order is authoritative on this path.
- `app/parser/loaders/docling_loader.py` mirrors the lazy-engine pattern of `ocr.py`.

## Key design properties
- **Parser independence** — every format returns the same DOM; new formats are new loaders only.
- **Idempotent + content-addressed** — `document_id = sha256(source)`; same bytes ⇒ same DOM.
- **Versioned** — `parser_version` + `dom_schema_version` in every DOM's `provenance`.
- **Lazy OCR** — the heavy engine only loads when a doc actually needs OCR.
- **Fallible + faithful** — unknown values are `None`, never fabricated (trust boundary).

## Docs / memory
- `docs/parser-module-spec.md` — full 25-field module spec.
- `project_memory/` — evolving decision log (master context, decisions, reading notes, questions, status, checkpoints).

## Notable open items
See `project_memory/questions.md` (stack choices are now confirmed; KG-phase contradiction is deferred by design).