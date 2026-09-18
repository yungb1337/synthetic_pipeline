# Production Document Parser Architecture Assessment

**Document ID:** `DOC-ARCH-ASSESS-2026-09-18`  
**Vertical:** Healthcare / Enterprise Knowledge Platform (**MedFactory AI**)  
**Scope:** Architecture assessment of Module #1 (Parser / Document Extraction Pipeline), Normalizer, Chunking, Routing, Storage, and Regulatory Compliance Readiness.

---

## Executive Summary

This document reviews the production document processing architecture of **MedFactory AI**. The system operates as a **modular monolith** with **page-centric concurrent execution**, designed to transform multi-format enterprise files into canonical, format-independent Document Object Models (DOMs), clean text, content-addressed retrieval chunks, and vector embeddings.

The pipeline achieves strong alignment with core production patterns:
1. **Zero Silent Data Loss:** Established via pre-scan `expected_page_set` page accounting and hard validation gates.
2. **Page-Centric Resource Governance:** Fast native operations execute across lightweight worker threads; heavy deep learning / OCR models execute in memory-bounded subprocess pools to contain C++ memory faults (`std::bad_alloc`).
3. **Multi-Tiered Storage & Audit Trails:** Raw source binaries, granular per-page extraction outputs, execution plans/ledgers, canonical DOMs, chunks, and embeddings are persisted separately and content-addressed.

Key architectural gaps identified are primarily downstream and regulatory:
- Transitioning routing from document-level heuristics to fine-grained per-page dynamic hybrid execution.
- Implementing structured field extraction against typed schemas (Module #5) with composite confidence scoring.
- Building a Human-in-the-Loop (HITL) review queue and cryptographic tamper-evident audit trails for regulatory compliance (e.g. 21 CFR Part 11).

---

## Step 1 — Pipeline Architecture & Component Mapping

### 1. High-Level Processing Flow

```
Raw File / Stream
       │
       ▼
1. Ingestion & Format Detection ───► [app/parser/mime.py, detection.py]
       │
       ▼
2. Pre-Parse Inspection & Routing ─► [app/routing/] (FastInspector + 9 Detectors + Scorer)
       │
       ▼
3. Source Scan & Planning ────────► [app/parser/source.py, planner.py] (Locks expected_page_set)
       │
       ▼
4. Page Scheduling & Execution ───► [app/parser/scheduler.py, page_result.py]
       │
       ├──► Native PDF Engine ────► [app/parser/engines/native_pdf.py] (PyMuPDF)
       ├──► Enrichment Engine ────► [app/parser/engines/enrichment.py, ocr.py] (RapidOCR)
       ├──► Heavy Docling Engine ─► [app/parser/engines/heavy_docling.py] (IBM Docling / TableFormer)
       └──► Simple / Image Engine ─► [app/parser/engines/simple.py, image.py]
       │
       ▼
5. Assembly & Validation Gate ────► [app/parser/assembler.py] (Hard gate: actual == expected pages)
       │
       ▼
6. DOM Builder & Semantic Graph ──► [app/parser/dom/builder.py, reading_order.py, reference_extractor.py]
       │
       ▼
7. Intermediate Storage & Ledger ─► [app/parser/storage.py, storage_pages.py]
       │
       ▼
8. Deterministic Text Normalizer ─► [app/normalizer/normalizer.py, rules.py]
       │
       ▼
9. Semantic Chunking ─────────────► [app/chunking/chunker.py, pipeline.py]
       │
       ▼
10. Embedding Projection ─────────► [app/embedding/embedder.py, runner.py] (BGE-M3 1024-dim)
       │
       ▼
11. Evaluation & LLM Judge ───────► [scripts/llm_judge.py] (Gemini light judge)
```

---

### 2. Module Details

#### 1. Ingestion & Format Detection
* **Description:** Sniffs binary magic bytes, inspects declared file extensions, and resolves canonical MIME types and internal slug identifiers. Rejects unsupported or corrupt headers before costly downstream operations.
* **Input / Output:** `(data: bytes, filename: str)` $\rightarrow$ `Detected` dataclass (`slug`, `mime`, `declared_extension`, `probe`, `unresolved: bool`).
* **Libraries / Models:** Standard library `mimetypes`, custom magic byte probe.
* **Code Location:** [`app/parser/mime.py`](app/parser/mime.py), [`app/parser/detection.py`](app/parser/detection.py).

#### 2. Pre-Parse Inspection & Document Routing
* **Description:** Performs open-without-render inspection (`fitz.open(stream=...)`) without rasterization or OCR. Evaluates 9 pluggable detectors (metadata, text density, image coverage, tables, multi-column layouts, OCR need, form fields, font distribution, and reading order). Computes an absolute weighted complexity score (0–100) and confidence score to assign the document to an execution band (`native`, `enrichment`, `docling`), with conservative escalation on low confidence.
* **Input / Output:** `(data: bytes, detected: Detected)` $\rightarrow$ `RoutingDecision` (`route`, `complexity_score`, `confidence`, `signals: list[Signal]`, `reasons`).
* **Libraries / Models:** `PyMuPDF` (`fitz`), `pydantic`.
* **Code Location:** [`app/routing/`](app/routing/) ([`inspectors.py`](app/routing/inspectors.py), [`detectors/`](app/routing/detectors/), [`scoring.py`](app/routing/scoring.py), [`policy.py`](app/routing/policy.py), [`router.py`](app/routing/router.py), [`schema.py`](app/routing/schema.py)).

#### 3. Source Scanning & Execution Planning
* **Description:** Inspects the document container before execution to determine the immutable `expected_page_set` (using `fitz.open().page_count`). Generates an `ExecutionPlan` consisting of discrete `PageWorkItem`s, writes the initial ledger (`manifest/<doc_id>/plan.json`), and checks for existing completed pages to support idempotent resumption.
* **Input / Output:** `(data: bytes, filename: str, route: str, decision: RoutingDecision)` $\rightarrow$ `SourceManifest` & `ExecutionPlan`.
* **Libraries / Models:** `PyMuPDF`, standard library `hashlib`, `pathlib`, `json`.
* **Code Location:** [`app/parser/source.py`](app/parser/source.py), [`app/parser/planner.py`](app/parser/planner.py).

#### 4. Page Scheduling & Resource Governance
* **Description:** Concurrently schedules `PageWorkItem`s across resource-governed pools. Lightweight `native` tasks run in a wide `ThreadPoolExecutor`; memory-intensive `docling` and `ocr` tasks run in a strictly bounded `ProcessPoolExecutor` (`heavy_concurrency` derived dynamically from available RAM) to prevent C++ heap exhaustion. Persists per-page results directly to disk.
* **Input / Output:** `(plan: ExecutionPlan)` $\rightarrow$ `list[PageResult]` (`doc_id`, `page_index`, `status`, `blocks`, `tables`, `images`, `timings`, `errors`).
* **Libraries / Models:** `concurrent.futures`, `psutil`, `multiprocessing`.
* **Code Location:** [`app/parser/scheduler.py`](app/parser/scheduler.py), [`app/parser/page_result.py`](app/parser/page_result.py).

#### 5. Format Loaders & Specialized Page Extraction Engines
* **Description:** Implements format-specific extraction engines:
  * **Native PDF Engine:** Extracts digital text spans, bounding boxes, font sizes, bold attributes, embedded image streams, and gridline/whitespace tables without rendering.
  * **Enrichment Engine:** Executes native extraction; if a page yields zero text blocks, renders a single Pixmap (downscaled to $\le 2000\text{px}$) and performs OCR.
  * **Heavy Docling Engine:** Invokes IBM Docling per page (`page_range=(p, p)`) for deep layout analysis and TableFormer neural table extraction; validates conversion status against empty stubs.
  * **Simple / Image Engines:** Handles plaintext, HTML, CSV, DOCX, and standalone images (PNG, JPG, TIFF).
* **Input / Output:** `(item: PageWorkItem)` $\rightarrow$ `PageResult` containing `list[RecoveredBlock]`, `list[RecoveredTable]`, `list[RecoveredImage]`.
* **Libraries / Models:** `PyMuPDF`, `RapidOCR` / ONNX Runtime (`rapidocr_onnxruntime`), `Docling` (IBM TableFormer), `python-docx`.
* **Code Location:** [`app/parser/engines/`](app/parser/engines/) ([`native_pdf.py`](app/parser/engines/native_pdf.py), [`enrichment.py`](app/parser/engines/enrichment.py), [`heavy_docling.py`](app/parser/engines/heavy_docling.py), [`simple.py`](app/parser/engines/simple.py), [`image.py`](app/parser/engines/image.py)), [`app/parser/ocr.py`](app/parser/ocr.py), [`app/parser/loaders/`](app/parser/loaders/).

#### 6. Assembly & Document Validation Gate
* **Description:** Folds per-page `PageResult`s into a unified `RecoveredDocument`. Enforces a strict validation gate: `assembled_page_set == expected_page_set`. If any page failed or is missing, triggers retry backoff; if retries are exhausted, marks the document as dead-lettered with an explicit error breakdown.
* **Input / Output:** `(plan: ExecutionPlan, results: list[PageResult])` $\rightarrow$ `AssemblyReport` & `RecoveredDocument`.
* **Libraries / Models:** `pydantic`.
* **Code Location:** [`app/parser/assembler.py`](app/parser/assembler.py).

#### 7. Canonical DOM Construction & Semantic Post-Processing
* **Description:** Translates extracted elements into the canonical Pydantic `Document` DOM:
  * *Stage 1 (Physical/Layout):* Spatial blocks, font attributes, row/cell bounding boxes, table captions, image references, and reading order chains.
  * *Stage 2 (Logical/Semantic):* Generates `reading_order_full` (interleaving blocks, tables, and images), extracts bibliographic citations (`reference_extractor.py`), cleans metadata, and attaches immutable `Provenance`.
* **Input / Output:** `(recovered: RecoveredDocument, document_id: str, sha256: str)` $\rightarrow$ `Document` DOM.
* **Libraries / Models:** `pydantic`, `re`.
* **Code Location:** [`app/parser/dom/builder.py`](app/parser/dom/builder.py), [`app/parser/dom/models.py`](app/parser/dom/models.py), [`app/parser/dom/reading_order.py`](app/parser/dom/reading_order.py), [`app/parser/dom/reference_extractor.py`](app/parser/dom/reference_extractor.py).

#### 8. Multi-Tiered Storage & Ledger Subsystem
* **Description:** Manages content-addressed, versioned filesystem persistence across isolated partitions:
  * `raw/<sha256>.<ext>`: Immutable raw source binary.
  * `pages/<doc_id>/p<index>/page-v0.1.0.docJSON`: Granular per-page extraction outputs.
  * `manifest/<doc_id>/plan.json`: Execution ledger (attempts, engine used, checksums, assembly report).
  * `dom/<doc_id>/dom-v<version>.docJSON`: Canonical parsed layout DOM.
  * `dom/<doc_id>/norm-v<version>.docJSON`: Normalized DOM.
  * `images/<doc_id>/<sha256>.<ext>`: Extracted image binaries.
  * `chunks/<doc_id>/chunks-v1.json`: Content-addressed semantic chunks.
  * `embeddings/<doc_id>/<embedder>/v1/vectors.npy` & `sidecar.json`: Dense vector matrices and validation stamps.
* **Input / Output:** Byte buffers, JSON models, NumPy arrays $\rightarrow$ Storage keys / file paths.
* **Libraries / Models:** Python file I/O, atomic write routines, `numpy`.
* **Code Location:** [`app/parser/storage.py`](app/parser/storage.py), [`app/parser/storage_pages.py`](app/parser/storage_pages.py), [`app/chunking/store.py`](app/chunking/store.py).

#### 9. Deterministic Text Normalizer
* **Description:** Applies deterministic, idempotent text-cleaning transformations across all text blocks: control character removal, Unicode NFKC normalization, hyphenated line-break repair, whitespace consolidation, and typographic quote/dash normalization. Preserves numbers, clinical units (e.g. mg/dL, $\mu\text{L}$, °C), and medical codes verbatim. Records transformation metrics in the `normalization_report`.
* **Input / Output:** Canonical `Document` DOM $\rightarrow$ Normalized `Document` DOM.
* **Libraries / Models:** Standard library `unicodedata`, `re`, `pydantic`.
* **Code Location:** [`app/normalizer/normalizer.py`](app/normalizer/normalizer.py), [`app/normalizer/rules.py`](app/normalizer/rules.py), [`app/normalizer/pipeline.py`](app/normalizer/pipeline.py).

#### 10. Semantic Chunking
* **Description:** Slices normalized DOM blocks into retrieval chunks using token-budget constraints, sentence boundary detection, and heading hierarchy anchors. Assigns content-addressed IDs ($\text{SHA256}(\text{doc\_id} + \text{text} + \text{source\_block\_ids})$), ensuring chunk identity remains independent of the embedding model.
* **Input / Output:** Normalized `Document` DOM $\rightarrow$ `ChunksArtifact` (`list[Chunk]`).
* **Libraries / Models:** HuggingFace tokenizers (`BAAI/bge-m3`), `pydantic`, `re`.
* **Code Location:** [`app/chunking/chunker.py`](app/chunking/chunker.py), [`app/chunking/pipeline.py`](app/chunking/pipeline.py), [`app/chunking/schema.py`](app/chunking/schema.py), [`app/chunking/tokenize.py`](app/chunking/tokenize.py), [`app/chunking/sentences.py`](app/chunking/sentences.py).

#### 11. Embedding Projection Seam
* **Description:** Batches un-embedded `chunk_id`s according to token budgets, computes dense 1024-dimensional vector representations, and writes Float32 NumPy matrices with validation sidecars.
* **Input / Output:** `ChunksArtifact` / text sequences $\rightarrow$ `numpy.ndarray` ($N \times 1024$ float32).
* **Libraries / Models:** `sentence-transformers` / PyTorch (`BAAI/bge-m3`), `numpy`.
* **Code Location:** [`app/embedding/embedder.py`](app/embedding/embedder.py), [`app/embedding/sbert.py`](app/embedding/sbert.py), [`app/embedding/runner.py`](app/embedding/runner.py).

#### 12. Batch Execution & Telemetry
* **Description:** Manages multi-threaded batch execution across corpora, maintains corpus manifests (`manifest.json`), manages memory recovery, and aggregates telemetry (P50/P95/P99 latency, route distribution, error categories).
* **Input / Output:** Corpus directory / manifest $\rightarrow$ `BatchReport`.
* **Libraries / Models:** `concurrent.futures`, `psutil`.
* **Code Location:** [`app/processing/executor.py`](app/processing/executor.py), [`app/processing/corpus.py`](app/processing/corpus.py).

#### 13. Stratified LLM Quality Judge
* **Description:** Executes automated post-parse semantic evaluation comparing parsed canonical DOMs against source PDFs. Evaluates 6 metrics: Completeness, Fidelity, Structure, Tables, References, and Scans/OCR. Emits structured verdicts (`PASS`, `PASS_WITH_ISSUES`, `FAIL`) with categorized defect listings.
* **Input / Output:** `(source.pdf, dom-v0.1.0.docJSON)` $\rightarrow$ `verdict.json`.
* **Libraries / Models:** Google GenAI SDK (`gemini-3.5-flash-lite`), `PyMuPDF`.
* **Code Location:** [`scripts/llm_judge.py`](scripts/llm_judge.py).

---

## Step 2 — Comparison Against Reference Production Patterns

| Reference Production Pattern | Codebase Implementation & Behavior | Status | Accuracy & Throughput Impact Analysis |
| :--- | :--- | :--- | :--- |
| **1. Dynamic Routing by Type / Complexity**<br>*(Born-digital extracted natively; scans/complex pages routed to OCR/heavy models only on low confidence)* | • [`app/routing/router.py`](app/routing/router.py) uses [`inspectors.py`](app/routing/inspectors.py) (open-only, zero-render) and 9 detectors to score complexity (0–100) and confidence.<br>• Assigns `native` (PyMuPDF), `enrichment` (native + fallback RapidOCR on 0-text pages), or `docling` (heavy deep learning).<br>• On low confidence, policy conservatively escalates one tier up.<br>• *Nuance:* Production router operates at document level; experimental branch ([`experimental/pdf_inspector_eval/`](experimental/pdf_inspector_eval/)) is benchmarking sub-30ms per-page dynamic hybrid routing. | **MATCH**<br>*(Strong alignment)* | • **Throughput Impact:** PyMuPDF processes pages in $\sim 5\text{--}15\text{ms}$ vs Docling's $\sim 1000\text{--}3000\text{ms}$. Bypassing Docling on $\sim 95\%+$ of standard pages saves $\sim 85\%$ RAM and eliminates C++ allocation crashes.<br>• **Accuracy Impact:** Retains vector-exact font data and coordinates for digital PDFs while ensuring scanned pages undergo OCR. |
| **2. Specialized Table Structure Recognition**<br>*(Table recognition as dedicated step with cell/row geometry, headers, and body deduplication)* | • In native path ([`native_pdf.py:118-170`](app/parser/engines/native_pdf.py#L118-L170)): PyMuPDF `find_tables()` with line and whitespace heuristics, oversplit column detection, and bounding box overlap deduplication ($>60\%$ overlap stripped from body text).<br>• In heavy path ([`docling_loader.py`](app/parser/loaders/docling_loader.py)): IBM Docling TableFormer (`FAST` mode) extracts full grid, `Row.bbox`, `Cell.bbox`, and captions.<br>• DOM represents tables as structured objects (`Table`, `Row`, `Cell`), not markdown strings. | **MATCH**<br>*(Functional match, with heuristic limitations on borderless native tables)* | • **Throughput Impact:** Native line extraction adds $<2\text{ms}$ overhead per page.<br>• **Accuracy Impact:** Eliminates duplicate text between tables and paragraphs. *Gap:* Complex or borderless tables processed on the native path can misalign without escalating to Docling or a dedicated lightweight table neural model. |
| **3. Separation of Layout Structure from Schema Field Extraction**<br>*(Two distinct stages to isolate layout failures from schema extraction failures)* | • [`app/parser/`](app/parser/) and [`app/normalizer/`](app/normalizer/) produce a schema-neutral, format-agnostic canonical DOM containing physical blocks, tables, images, and reading order.<br>• Field extraction against clinical schemas (e.g. EHR/FHIR, lab panels) is isolated in downstream Module #5 ("Knowledge Extraction / KG") and not coupled to parsing. | **MATCH**<br>*(Clean architectural boundary)* | • **Audit & Traceability Impact:** Critical for root-cause diagnosis. Isolates whether a missing clinical field was dropped during layout parsing or misclassified during downstream extraction. |
| **4. Confidence Scoring & Automated Acceptance / HITL Routing**<br>*(Per-document/field confidence scores, auto-accepting high-confidence and routing low-confidence to human review)* | • Router outputs `RoutingDecision.confidence`.<br>• OCR engine emits per-block `confidence` scores.<br>• Docling loader computes `_table_structural_confidence`.<br>• [`scripts/llm_judge.py`](scripts/llm_judge.py) computes 6 multi-metric quality scores.<br>• *Divergence:* There is **no automated Human-in-the-Loop (HITL) review queue, triage API, or field-level validation threshold engine**. | **DIVERGENCE**<br>*(Missing human review workflow & automated acceptance gate)* | • **Accuracy & Compliance Impact:** Critical for regulatory compliance. Currently, low-confidence documents escalate to heavier models or are dead-lettered. Without a dedicated HITL queue and validation thresholds, edge cases in the low-confidence tail cannot be audited or corrected by clinical reviewers. |
| **5. Separate Storage of Intermediate Artifacts for Audit**<br>*(Independent storage of raw files, page results, execution plans, DOMs, chunks, embeddings, and confidence)* | • [FilesystemStore](app/parser/storage.py) & [PageStore](app/parser/storage_pages.py) persist immutable raw binaries (`raw/`), per-page intermediate outputs (`pages/`), execution journals (`manifest/<doc_id>/plan.json`), parsed DOMs (`dom/`), normalized DOMs (`dom/norm-*`), chunks (`chunks/`), and vector embeddings (`embeddings/`).<br>• Full provenance metadata is attached to every DOM and chunk. | **MATCH**<br>*(Fully implemented and verified)* | • **Audit & Traceability Impact:** Supports regulatory compliance and zero silent data loss. Every synthetic data point or embedding is traceable to source page coordinates, OCR engine versions, and cryptographic hashes. |

---

## Step 3 — Recommendations & Actionable Roadmap

| Module | Current Behavior | Gap | Recommendation | Priority |
| :--- | :--- | :--- | :--- | :--- |
| **Human Review & Triage Queue (HITL)** | Low-confidence or failed documents are recorded in `manifest/<doc_id>/plan.json` or dead-lettered; no human review interface or routing exists. | **Missing HITL Routing & Triage Seam:** No mechanism for human reviewers to inspect, edit, or sign off on low-confidence extractions before synthetic data generation. | **Add New Module (`app/triage/`):**<br>• Implement a review queue service that ingests documents scoring below defined confidence thresholds.<br>• Store review state, clinical reviewer signatures, and manual corrections in an immutable audit ledger (`audit/<doc_id>/review.json`). | **HIGH**<br>*(Regulatory requirement for compliance)* |
| **Field-Level Schema Extraction & Validation Gate** | Parser outputs structural DOM (`blocks`, `tables`); field extraction against clinical schemas is unbuilt (planned for Module #5). | **Missing Field-Level Extraction & Scoring:** No typed Pydantic/FHIR schema validation or field-level extraction confidence scoring. | **Add New Module (`app/extraction/` or Module #5):**<br>• Ingest normalized DOM and extract structured entities against versioned schemas.<br>• Compute confidence per field (combining OCR confidence, layout confidence, and LLM extraction logprobs).<br>• Enforce strict schema validation rules (e.g. valid unit ranges, required fields). | **HIGH**<br>*(Data integrity & compliance)* |
| **Confidence Scoring & Acceptance Threshold Engine** | Confidence is computed at routing time, OCR block level, table heuristic level, and LLM-judge level, but remains isolated without a unified document score. | **Fragmented Confidence Aggregation:** No single composite document confidence score that governs auto-acceptance vs. manual review escalation. | **Modify `app/routing/scoring.py` & `app/parser/assembler.py`:**<br>• Add an aggregate document confidence model combining router confidence, page OCR confidences, table structural integrity, and layout consistency.<br>• Emit an automated acceptance flag (`auto_accept: bool`, `confidence >= 0.85`) into the DOM `Provenance`. | **HIGH**<br>*(Auditability & automated throughput)* |
| **Per-Page Dynamic Hybrid Routing** | Document-level router sets the primary execution tier for the whole document; per-page variation is handled via enrichment fallback. | **Document-Level Routing Granularity:** Multi-page documents with 1 scanned page or 1 complex table currently route the whole document or rely on post-scan fallbacks. | **Modify `app/routing/` & `app/parser/planner.py`:**<br>• Productionize the per-page classification tested in [`experimental/pdf_inspector_eval/smart_router.py`](experimental/pdf_inspector_eval/smart_router.py).<br>• Allow `ExecutionPlan` to assign heterogeneous engines per page (`p0: native`, `p1: heavy_docling`, `p2: native`) directly during the planning phase. | **MEDIUM**<br>*(Improves throughput by $\sim 3\text{--}5\times$ on mixed-layout documents)* |
| **Lightweight Table Recognition Tier (P003 / Tatr)** | Native PyMuPDF line-table heuristic handles simple tables; complex borderless tables escalate to heavy Docling / TableFormer. | **Table Engine Gap between Heuristic and Heavy DL:** Borderless academic/medical tables on native routes risk cell misalignments if Docling is skipped. | **Modify `app/parser/engines/native_pdf.py` / Add Lightweight Table Seam:**<br>• Incorporate a fast, lightweight table structure recognizer (or enhance the whitespace/coordinate boundary analyzer as prototyped in P003) for borderless tables.<br>• Avoids invoking heavy Docling solely for basic borderless tables. | **MEDIUM**<br>*(Improves table accuracy and saves RAM)* |
| **Audit Trail & Provenance Signing** | DOM and chunks record version strings, hashes, and timestamps in plain JSON. | **Cryptographic Tamper-Evidence:** Lacks HMAC or digital signatures over intermediate extraction artifacts. | **Modify `app/parser/storage.py` & `app/parser/dom/models.py`:**<br>• Add an optional cryptographic signature/digest block to `Provenance` covering source hash, parser version, and extracted fields for 21 CFR Part 11 regulatory readiness. | **HIGH**<br>*(Regulatory audit trail)* |

---
*Assessment completed on 2026-09-18. Registered in repository memory index.*
