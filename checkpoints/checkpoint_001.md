# Checkpoint 001 — Parser Module (Extraction → DOM)

**Date:** 2026-08-02 · **Milestone:** Module #1 built, tested, running end-to-end.

## Summary
Implemented the first pipeline module of the Synthetic Data Factory: a modular-monolith **Parser** that turns any supported file into a canonical, parser-independent **Document Object Model (DOM)** in a single read. Scope locked to `Extraction → DOM` only (per SYN4); normalization/chunking/KG are separate future modules.

## Architecture decisions made this checkpoint
1. **Stack confirmed** (user): PDF = PyMuPDF; OCR = on-prem RapidOCR; scope = Extraction→DOM; storage = Store abstraction w/ Filesystem default.
2. **Single-pass extraction**: detect → load once → build DOM. Never re-read the file (SYN4).
3. **Canonical DOM** as the parser boundary. New format = new loader only (SYN4).
4. **Reading Order Graph** = in-memory ordered chain of block ids (SYN4: not Neo4j).
5. **OCR is lazy** — engine loads only when a document needs it; unloadable/absent engine never blocks other formats.
6. **Idempotent + content-addressed**: `document_id = sha256(source)`, deterministic outputs.
7. **Versioned provenance**: `parser_version` + `dom_schema_version` in every DOM.

## Completed work
- venv + deps (pymupdf, pillow, openpyxl, pydantic, pytest, rapidocr-onnxruntime).
- `app/parser/`: config, detection, parts, dom (models/builder/reading_order), loaders, ocr, storage, events, extraction, cli.
- Format loaders: PDF, DOCX, XLSX, CSV/TSV, JSON, XML, HTML, Markdown, plaintext, image (OCR).
- CLI end-to-end validated on real PDF/CSV/MD samples; DOM + raw persisted content-addressed.
- Tests: 7 passing (detection, CSV, markdown, PDF, idempotency, unsupported, store-writes).

## Remaining work (next module order)
1. Text Normalization & Cleaning (its own module)
2. Semantic chunking
3. Embeddings
4. Knowledge extraction → ontology mapping → KG
5. Validation framework; generation; versioning/lineage; multi-tenancy; APIs.

## Risks / open
- **KG contradiction** (SYN1/2 vs SYN3) deliberately deferred to KG phase — not resolved here.
- `parser_out/` default CLI output and `_samples/` are git-ignored.
- Folder name 22_07 → `synthetic_pipeline` rename is BLOCKED while the editor holds CWD; do it after closing (command in session).

## Important discoveries
- RapidOCR (onnxruntime 1.28) installs and loads on Python 3.14.6.
- PyMuPDF `page.find_tables()` returns tables robustly; heading-by-font-size works for PDFs.

## Next recommended steps
1. Confirm/do the folder rename (repo hygiene).
2. Read the 4 academic PDFs properly (subagent returned empty) — ground the normalization/KG decisions.
3. Plan Module #2 (Normalization) as its own spec + implementation.