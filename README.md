# Synthetic Data Factory (MedFactory AI) — Enterprise Pipeline Overview

An enterprise-grade **Document Understanding & Synthetic Data Engine** built for maximum **trust, data integrity, explainability, and high throughput**. The platform ingests unstructured, multi-modal enterprise documents (medical literature, clinical trials, legal filings, technical specifications) and transforms them into a canonical, parser-independent **Document Object Model (DOM)**, clean normalized DOMs, structurally grounded semantic chunks, and vector embeddings for downstream retrieval, knowledge graph construction, and synthetic data generation.

---

## Status & Capabilities

| Module / Component | Function | Status | Key Features |
|---|---|---|---|
| **Module #1 — Parser (Page-Centric)** | Ingestion & Extraction → Canonical DOM | **Production-Ready** | Page-centric streaming (ADR-013), `ResourceGovernor`, PyMuPDF native vector engine, Docling TableFormer backend, zero silent loss verification. |
| **Module #2 — Normalizer** | DOM → Clean DOM | **Production-Ready** | Pure, idempotent rules (`strip_controls`, `nfkc`, `dehyphenate`, `ws`, `typography`) with complete mutation provenance reporting. |
| **Module #3 — Smart Router** | Per-Page Quality & Engine Selection | **Production-Ready** | 3-tier per-page routing (ADR-001/011), `FastInspector` feature extraction, Two-Tier Table Escalation (ADR-015), full diagnostics in `Provenance.routing`. |
| **Module #4 — Semantic Chunking** | Clean DOM → Grounded Chunks | **Production-Ready** | Structural DOM anchoring (~400-token target, 2048 hard cap, heading seam overlap, PySBD sentence splitting), content-addressed `ChunkStore`. |
| **Embedding Seam** | Vector Embedding Pipeline | **Production-Ready** | Local `BAAI/bge-m3` (1024-dim, multilingual) CUDA fp16 execution, automatic CPU fallback, token-budgeted `ChunkEmbedPipeline`. |
| **On-Prem OCR Engine** | Scanned / Mixed Document Extraction | **Unified RapidOCR v6** | On-demand PP-OCRv6 engine running locally via ONNXRuntime across native, enrichment, and Docling paths. |
| **Batch / Scale Layer** | High-Throughput Corpus Execution | **Production-Ready** | Worker pool, parallel SHA-256 hashing, durable incremental manifest, graceful backoff, and crash-resilient checkpoints. |

---

## System Architecture (Modular Monolith)

```
app/
  parser/         # Module #1 — Ingestion & Page-Centric Extraction → Canonical DOM
    config.py     # Parser configuration (layout_backend: "auto" | "native" | "docling", OCR toggles)
    detection.py  # Format detection (magic bytes → container probe → content sniff → extension last)
    source.py     # SourceDocument abstraction (page count, checksums, format validation)
    planner.py    # PagePlanner: two-tier table probe & per-page execution plan (ADR-013, ADR-015)
    scheduler.py  # PageScheduler & ResourceGovernor: dynamic RAM-bounded concurrency
    engines/      # Isolated per-page execution backends
      native_pdf.py   # PyMuPDF fast native extractor (P1 headings, P4 margins, P5 tables)
      docling_pdf.py  # Docling TableFormer backend for complex borderless structures
      enrichment.py   # Hybrid native + RapidOCR for scanned / image-bearing pages
      docx.py, html.py, txt.py, etc. # Structured document format engines
    assembler.py  # DocumentAssembler & DocumentValidator: anti-silent-loss verification
    dom/          # Canonical models (Document, Block, Section, Page, Table, Image, Provenance)
    loaders/      # Format-specific loaders (Docling, PDF, DOCX, XLSX, CSV, JSON, XML, HTML, MD)
    ocr.py        # Unified RapidOCR v6 engine (lazy, on-demand local ONNXRuntime)
    storage.py    # Content-addressed FilesystemStore (`dom/{doc_id}/dom-v{version}.docJSON`)
    extraction.py # Public API entry point

  normalizer/     # Module #2 — Text Normalization & Cleaning
    rules.py      # Idempotent rules (strip_controls, NFKC, dehyphenate, whitespace, typography)
    normalizer.py # DOM → Normalized DOM + detailed provenance report
    cli.py        # CLI for standalone DOM normalization

  routing/        # Module #3 — Intelligent Document Router
    config.py     # Calibrated RoutingConfig weights & score band thresholds
    inspectors.py # FastInspector: cheap PyMuPDF feature extraction (no render)
    detectors/    # 9 pluggable detectors (Metadata, Text, Image, Layout, OCR, Table, Form, Font, ReadingOrder)
    scoring.py    # Absolute-sum complexity scoring (0–100 scale)
    policy.py     # 3-band routing policy (0-30 Native / 31-60 Enrichment / 61-100 Docling)
    router.py     # Aggregates signals → RoutingDecision persisted into Provenance.routing

  chunking/       # Module #4 — DOM-Anchored Semantic Chunking
    config.py     # ChunkingConfig (~400-token target, 2048 token hard cap, heading seam overlap)
    chunker.py    # Heading hierarchy & DOM structural block chunker
    sentences.py  # PySBD sentence segmentation preserving sentence boundaries
    tokenize.py   # Fast HuggingFace / tiktoken tokenizer & budget tracker
    store.py      # ChunkStore interface & FilesystemChunkStore (retrieval-grounding seam)
    pipeline.py   # ChunkEmbedPipeline (idempotent chunking + embedding)

  embedding/      # Embedding Integration Seam
    embedder.py   # Embedder protocol (list-in → vectors-out)
    sbert.py      # SentenceTransformerEmbedder (BAAI/bge-m3 on PyTorch CUDA / CPU)
    runner.py     # batch_embed() with token-budget batching (≤16k tokens / ≤32 texts)
    factory.py    # default_embedder() picking local GPU model or DummyEmbedder fallback

  processing/     # Batch & Scale Processing Layer
    corpus.py     # Corpus scanning, parallel SHA-256 hashing, and durable manifest
    executor.py   # ThreadPoolExecutor worker pool with retries, backoff, and progress flushing
```

---

## Page-Centric Execution & Anti-Silent-Loss Architecture (ADR-013)

To eliminate `std::bad_alloc` crashes on heavy PDF corpora and prevent silent drop defects:
1. **Page as Processing & Storage Unit:** Documents are broken down into single-page execution units (`page_range=(p, p)`), bounding C++ heap allocations.
2. **Resource Governor:** Dynamically measures available host RAM before executing memory-heavy neural backends (Docling TableFormer), restricting heavy worker concurrency to `1` on memory-constrained systems.
3. **Anti-Silent-Loss Gate (`DocumentValidator`):** Every assembled document is mathematically verified against source page counts. If any page fails extraction, the entire document is flagged and recorded to a dead-letter ledger rather than silently returning a truncated DOM.

---

## 3-Tier Per-Page Smart Routing & Two-Tier Table Escalation

The router assesses document complexity prior to parsing to direct each page to the most cost-effective and accurate engine:

```
                  ┌─────────────────────────────────────┐
                  │          Source Document            │
                  └──────────────────┬──────────────────┘
                                     │
                                     ▼
                  ┌─────────────────────────────────────┐
                  │      Fast Feature Inspection        │
                  │ (Text density, fonts, images, lines)│
                  └──────────────────┬──────────────────┘
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         ▼                           ▼                           ▼
┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
│  Tier 1: Native  │       │Tier 2: Enrichment│       │  Tier 3: Docling │
│  (0–30 Score)    │       │  (31–60 Score)   │       │  (61–100 Score)  │
├──────────────────┤       ├──────────────────┤       ├──────────────────┤
│ Clean digital    │       │ Scans & mixed    │       │ Multi-column,    │
│ text, structured │       │ digital pages    │       │ complex forms,   │
│ formats, simple  │       │ (Native + OCR    │       │ borderless dense │
│ bordered tables  │       │ fallback)        │       │ neural tables    │
│ (~35–45 p/s)     │       │ (~5–10 p/s)      │       │ (~1–2 p/s)       │
└──────────────────┘       └──────────────────┘       └──────────────────┘
```

### Two-Tier Table Escalation (ADR-015 / P2)
Table-bearing pages are probed during planning using PyMuPDF vector line analysis (`find_tables(strategy="lines")`):
- **Simple Bordered Tables:** Clean rectangular grids with explicit border lines remain on the high-speed native path (~30–40 p/s).
- **Complex / Borderless Tables:** Tables lacking vector borders or exhibiting multi-line cell wraps escalate to Docling TableFormer for neural cell-snapping.
- **Impact:** Reduces heavy TableFormer workload from **20.79%** to **7.55%** of pages (>63% reduction) while preserving full structural precision.

---

## Parser Quality Enhancements (P1, P2, P4, P5)

* **P1 — Probabilistic Heading Classification (`native_pdf.py`):** Replaces naive font-size thresholds with length bounds, punctuation density rejection (>50%), sentence termination rules (`.`, `?`, `!`), word count caps (>25 words forced to paragraph), and consecutive heading hierarchy smoothing.
* **P2 — Two-Tier Table Escalation (`planner.py`):** Keeps simple bordered tables on the native path, escalating only complex borderless structures.
* **P4 — Margin & Metadata Suppression (`native_pdf.py`):** Automatically suppresses journal metadata (`OPEN ACCESS`, `Citation:`, `DOI:10.`, `Received:`, `Accepted:`, etc.) within top/bottom 10% margins to keep body reading order pristine.
* **P5 — Table Unicode NFC & Cell Wrapping (`docling_loader.py` & `native_pdf.py`):** Enforces Unicode NFC normalization to preserve scientific symbols (`±`, `≥`, `≤`, `~`, `→`, `≈`, `≠`, `µ`, `°`, `α`, `β`, `γ`) and collapses wrapped multi-line cell whitespace.

---

## Empirical Benchmark & Evaluation Results

### 1. Dual-Corpus Benchmark (1,945 Documents / 25,865 Pages)
- **Parse Success Rate:** **100%** (1,945/1,945 documents, zero crashes, zero silent page drops).
- **Memory Footprint:** Peak RSS bounded below **2.5 GB**.
- **LLM Judge Pass Rate:** **95.9%** across dual corpora (Corpus A medical & Corpus B heterogeneous).

### 2. Targeted Issue Cohort Re-Evaluation (250 Defect Documents / 3,680 Pages)
Re-evaluation of the 250 highest-priority defect documents following P1/P2/P4/P5 improvements using multi-key LLM judging:

| Evaluation Metric | Pre-Fix Score | Post-Fix Score | Delta |
| :--- | :--- | :--- | :--- |
| **Pass Rate (PASS / PASS_WITH_ISSUES)** | **50.0%** (125 / 250) | **98.4%** (246 / 249 valid) | **+48.4 pp** |
| **Defect Fails** | 125 | 3 | **-97.6%** |
| **Structure Score** | 41.9% | **92.7%** | **+50.8 pp** |
| **Table Quality Score** | 34.6% | **77.2%** | **+42.6 pp** |
| **Fidelity / Integrity Score** | 53.3% | **96.3%** | **+43.0 pp** |
| **Completeness Score** | 54.3% | **95.1%** | **+40.8 pp** |
| **References Score** | 53.6% | **94.1%** | **+40.5 pp** |
| **Docling Escalation Rate** | 20.79% | **7.55%** (278 / 3,680 pages) | **-13.24 pp** (>63% reduction) |
| **Live Pipeline Throughput** | 1.59 p/s | **1.96 p/s** (3,680 pages) | **1.2x speedup** |

---

## Semantic Chunking & Embedding Seam

Semantic chunking transforms normalized DOMs into content-addressed chunks (`chunk_id = sha256(...)`):
- **DOM Anchoring:** Every chunk retains precise provenance referencing `doc_id`, structural `block_ids`, `section_id`, `heading_path`, and `page_numbers`.
- **Structural Integrity:** Respects section headers and sentence boundaries without slicing mid-sentence or mid-heading.
- **Heading Seam Overlap:** Preserves section context across chunk boundaries (~48 tokens overlap).
- **Local GPU Embeddings:** Chunks are projected into 1024-dimensional vectors using **`BAAI/bge-m3`** loaded locally on PyTorch CUDA (RTX 3050 fp16). `ChunkEmbedPipeline` prevents redundant re-embedding.

---

## Supported Input Formats

- **Documents:** PDF (vector text, complex layouts, headings, tables, scans), DOCX, XLSX, CSV, TSV, JSON, XML, HTML, Markdown, Plain Text (`.txt`).
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

# Install GPU / Sentence-Transformers support (CUDA)
pip install -r requirements-gpu.txt

# Install Docling layout engine
pip install -r requirements-docling.txt

# Download BGE-M3 embedding weights into models/
PYTHONPATH=. python scripts/download_models.py
```

### 2. Execution Commands

#### Single Document / Directory Parsing:
```bash
python -m app.parser.cli --in path/to/document.pdf --out parser_out
```

#### Document Normalization:
```bash
python -m app.normalizer.cli --dom parser_out/dom/<doc_id>/dom-v0.1.0.docJSON --out parser_out/normalized/<doc_id>.json
```

#### Semantic Chunking & Embedding:
```bash
# Chunk only:
python -m app.chunking.cli --doc <doc_id> --store parser_out

# Chunk and compute BGE-M3 embeddings:
python -m app.chunking.cli --doc <doc_id> --store parser_out --embed
```

#### High-Throughput Batch Execution:
```bash
python -m app.processing.cli --in path/to/corpus --out store_out --concurrency 8
```

#### Verification & Test Suite:
```bash
# Verify GPU embedder:
python scripts/check_embedder.py

# Run full test suite:
pytest
```

---

## Key Guarantees & Design Principles

- **100% On-Premise & Privacy-Preserving:** No document, text, chunk, or vector ever leaves the local environment. All OCR, layout, and embedding models execute on-prem.
- **Parser Independence:** Downstream modules consume the unified, canonical DOM (`Document`), isolated from source format quirks.
- **Idempotency & Content Addressing:** `document_id = sha256(source)` and `chunk_id = sha256(content + provenance)`. Identical input always yields identical outputs.
- **Zero Silent Data Loss:** Rigorous document validation guarantees that page-level failures trigger dead-letter logging rather than silent truncation.
- **Full Lineage & Auditability:** Complete provenance preserved across extraction, normalization, routing, and chunking stages.

---

## Architectural Decision Records (ADRs)

Key architectural decisions are formally documented in [`project_memory/architecture_decisions.md`](project_memory/architecture_decisions.md) and [`docs/adr/`](docs/adr/):
- **ADR-001:** 3-Tier Per-Page Smart Routing Architecture
- **ADR-007:** Docling as a Gated Layout & Table Engine
- **ADR-009:** Canonical DOM Structural Grounding for Chunking
- **ADR-010:** Local Multilingual BGE-M3 Embedding Seam
- **ADR-011:** Intelligent Document Router Complexity Scoring & Bands
- **ADR-012:** Enrichment Band Architecture (Native + OCR Fallback)
- **ADR-013:** Page-Centric Execution Model & Anti-Silent-Loss Gate
- **ADR-014:** Production Router Calibration & Benchmark Evaluation
- **ADR-015:** Parser Quality Enhancements (P1/P4/P5) & Two-Tier Table Escalation (P2)
