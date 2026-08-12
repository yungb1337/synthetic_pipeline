# Synthetic Data Factory (MedFactory AI) — Pipeline Overview

An enterprise **Synthetic Data Factory** designed for maximum **trust, privacy, and explainability**. The platform ingests proprietary, unstructured enterprise documents (healthcare, legal, technical) and transforms them into a canonical, parser-independent **Document Object Model (DOM)**, clean normalized DOMs, semantic chunks, and vector embeddings for downstream retrieval, knowledge graph construction, and synthetic data generation.

---

## Status & Capabilities

| Module / Component | Function | Status | Key Features |
|---|---|---|---|
| **Module #1 — Parser** | Extraction → Canonical DOM | **Implemented & Tested** | PyMuPDF, layout analysis, DOM models (`Document`, `Block`, `Table`, `Image`, `Provenance`), lazy OCR. |
| **Module #2 — Normalizer** | DOM → Clean DOM | **Implemented & Tested** | Pure, idempotent rules (`strip_controls`, `nfkc`, `dehyphenate`, `ws`, `typography`) + modification reporting. |
| **Module #3 — Intelligent Router** | Quality & Pipeline Selection | **Implemented & Tested** | `FastInspector` + 9 pluggable detectors + complexity scoring → 3 routing bands (`Native`, `Enrichment`, `Docling`). |
| **Module #4 — Semantic Chunking** | Clean DOM → Grounded Chunks | **Implemented & Tested** | Structural DOM anchoring (~400-token target, 2048 hard cap, heading seam overlap, PySBD sentence splitting), `ChunkStore`. |
| **Embedding Seam** | Vector Embedding Pipeline | **Implemented & Tested** | Local `BAAI/bge-m3` (1024-dim, multilingual) CUDA fp16 (RTX 3050), automatic CPU fallback, `ChunkEmbedPipeline`. |
| **On-Prem OCR Engine** | Scanned / Image Extraction | **Unified on RapidOCR v6** | On-demand PP-OCRv6 engine running locally via ONNXRuntime across native, enrichment, and Docling paths. |
| **Batch / Scale Layer** | High-Throughput Corpus Execution | **Implemented & Tested** | Worker pool, parallel hashing, persistent `{sha256}` manifest for incremental/crash-safe processing. |

---

## System Architecture (Modular Monolith)

```
app/
  parser/         # Module #1 — Ingestion & Extraction → Canonical DOM
    config.py     # Parser configuration (layout_backend: "auto" | "native" | "docling", OCR toggles)
    detection.py  # Format detection (magic bytes → container probe → content sniff → extension last)
    dom/          # Models (Document, Block, Section, Page, Table, Image, Provenance), ROG, DOM builder
    loaders/      # Format-specific loaders (PDF, DOCX, XLSX, CSV, JSON, XML, HTML, MD, TXT, Docling)
    ocr.py        # Unified RapidOCR v6 engine (lazy, on-demand local ONNXRuntime)
    storage.py    # Content-addressed FilesystemStore (`dom/{doc_id}/dom-v{version}.docJSON`)
    extraction.py # Main entry point: Detect → Inspect/Route → Load → Build → Store → Emit

  normalizer/     # Module #2 — Text Normalization & Cleaning
    rules.py      # Idempotent rules (strip_controls, NFKC, dehyphenate, whitespace, typography)
    normalizer.py # DOM → Normalized DOM + detailed provenance report
    cli.py        # CLI for stand-alone DOM normalization

  routing/        # Module #3 — Intelligent Document Router
    config.py     # Calibrated RoutingConfig weights & score band thresholds
    inspectors.py # FastInspector: cheap PyMuPDF feature extraction (no render)
    detectors/    # 9 pluggable detectors (Metadata, Text, Image, Layout, OCR, Table, Form, ReadingOrder, Font)
    scoring.py    # Absolute-sum complexity scoring (0–100 scale)
    policy.py     # 3-band routing policy (0-30 Native / 31-60 Enrichment / 61-100 Docling)
    router.py     # Aggregates signals → RoutingDecision persisted into Provenance.routing

  chunking/       # Module #4 — DOM-Anchored Semantic Chunking
    config.py     # ChunkingConfig (~400-token target, 2048 token hard cap, heading seam overlap)
    chunker.py    # Heading hierarchy & DOM structural block chunker
    sentences.py  # PySBD sentence segmentation preserving sentence boundaries
    tokenize.py   # Fast tiktoken/HuggingFace tokenizer & budget tracker
    store.py      # ChunkStore interface & FilesystemChunkStore (retrieval-grounding seam)
    pipeline.py   # ChunkEmbedPipeline (idempotent chunking + embedding)

  embedding/      # Embedding Integration Seam
    embedder.py   # Embedder protocol (list-in → vectors-out)
    sbert.py      # SentenceTransformerEmbedder (BAAI/bge-m3 on PyTorch CUDA / CPU)
    runner.py     # batch_embed() with token-budget batching (≤16k tokens / ≤32 texts)
    factory.py    # default_embedder() picking local GPU model or DummyEmbedder fallback

  processing/     # Batch & Scale Processing Layer
    corpus.py     # Corpus scanning, parallel SHA256 hashing, and durable manifest
    executor.py   # ThreadPoolExecutor worker pool with retries, backoff, and progress flushing
```

---

## Intelligent Document Routing (3-Band Policy)

The router evaluates document complexity via `FastInspector` before full parsing to dispatch documents to the optimal, most cost-effective extraction path:

1. **Native Band (Score 0–30):** Direct, high-speed extraction via PyMuPDF / native loaders for clean digital PDFs and structured formats.
2. **Enrichment Band (Score 31–60):** Native extraction augmented with targeted RapidOCR v6 for pages that yield no text blocks (e.g. mixed digital PDFs with scanned inserts).
3. **Docling Band (Score 61–100):** Deep layout, table-structure, and reading-order recovery using local Docling layout models (gated for complex multi-column documents, academic papers, and complex forms).

> **Explainable Routing:** Every decision persists complete diagnostics into `Provenance.routing`, including complexity scores, confidence, triggered reasons, individual detector signals, and detector/policy versions (`router_v`, `policy_v`, `scoring_v`).

---

## Semantic Chunking & Embedding Seam

Semantic chunking converts normalized DOMs into content-addressed chunks (`chunk_id = sha256(...)`):
- **DOM Anchoring:** Every chunk retains precise provenance referencing `doc_id`, structural `block_ids`, `section_id`, `heading_path`, and `page_numbers`.
- **Structural Integrity:** Respects section headers and sentence boundaries without slicing mid-sentence or mid-heading.
- **Heading Seam Overlap:** Preserves section context across chunk boundaries (~48 tokens overlap).
- **GPU Embeddings:** Chunks are projected into 1024-dimensional vectors using **`BAAI/bge-m3`** loaded locally on PyTorch CUDA (RTX 3050 fp16). `ChunkEmbedPipeline` prevents redundant re-embedding.

---

## Supported Input Formats
- **Documents:** PDF (text, layout, headings, tables, scanned), DOCX, XLSX, CSV, TSV, JSON, XML, HTML, Markdown, Plain Text (`.txt`).
- **Images:** PNG, JPG, JPEG, TIFF, BMP (processed on-prem via RapidOCR v6).

---

## Quick Start & Usage

### 1. Environment Setup
```bash
# Create and activate virtual environment
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate

# Install core dependencies
pip install -r requirements.txt

# Install GPU / Sentence-Transformers support (RTX / CUDA)
pip install -r requirements-gpu.txt

# Download BGE-M3 embedding weights into models/
PYTHONPATH=. python scripts/download_models.py
```

### 2. Optional Docling Layout Engine Setup
```bash
pip install -r requirements-docling.txt
```

### 3. Execution Commands

#### Single Document / Directory Parsing:
```bash
python -m app.parser.cli --in path/to/document.pdf --out parser_out
```

#### Document Normalization:
```bash
python -m app.normalizer.cli --dom parser_out/dom/<doc_id>/dom-v1.docJSON --out parser_out/normalized/<doc_id>.json
```

#### Semantic Chunking & Embedding:
```bash
# Chunk only:
python -m app.chunking.cli --doc <doc_id> --store parser_out

# Chunk and compute BGE-M3 embeddings:
python -m app.chunking.cli --doc <doc_id> --store parser_out --embed
```

#### Batch Processing over a Large Corpus:
```bash
python -m app.processing.cli --in path/to/corpus --out store_out --concurrency 8
```

#### Verification & Test Suite:
```bash
# Verify GPU embedder:
python scripts/check_embedder.py

# Run full test suite:
python -m pytest
```

---

## Key Guarantees & Design Principles

- **100% On-Premise & Privacy-Preserving:** No document, text, chunk, or vector ever leaves the local machine. All OCR and embedding models run locally.
- **Parser Independence:** Downstream modules consume the unified, canonical DOM (`Document`), completely isolated from source format quirks.
- **Idempotency & Content Addressing:** `document_id = sha256(source)` and `chunk_id = sha256(content + provenance)`. Identical input always yields identical outputs.
- **Faithful & Fallible:** Unknown values are represented as `None` without halluncinations (strict trust boundary).
- **Full Lineage & Auditability:** Complete provenance preserved across extraction, normalization, routing, and chunking stages.

---

## Documentation & Project Memory

- `docs/parser-module-spec.md` — Canonical DOM specification & extraction pipeline.
- `docs/normalizer-module-spec.md` — Text normalization rules & provenance specification.
- `docs/routing-spec.md` — Intelligent Document Router design & scoring spec.
- `docs/scale-batch-spec.md` — High-throughput batch processing & worker pool architecture.
- `project_memory/` — Architectural Decision Records (ADRs), module status, and team blackboard.