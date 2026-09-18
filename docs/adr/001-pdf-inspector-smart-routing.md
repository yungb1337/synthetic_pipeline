# ADR-001: 3-Tier Per-Page Smart Routing with Rust Inspection and Single-Page TableFormer Escalation

**Status:** Accepted  
**Date:** 2026-09-18  
**Author:** MedFactory AI Core Engineering Team  
**Scope:** `app/routing/`, `app/parser/`, `experimental/pdf_inspector_eval/`  
**Supersedes/Extends:** Extends ADR-007 (Hybrid Engine Routing), ADR-011 (Config-Driven Routing), ADR-013 (Page-Centric Monolith)

---

## 1. Context & Problem Statement

In academic, medical, and clinical PDF processing (e.g., PubMed Central, FDA clinical trial filings, medical journals, scanned hospital receipts), documents exhibit extreme layout heterogeneity:
1. **Corrupt Font CMaps**: Older archive PDFs frequently lack valid `ToUnicode` CMaps or contain broken hex glyphs, causing silent text loss during native extraction.
2. **Tabular Complexity vs Throughput Trade-off**: Dense clinical tables require neural structure recognition (Docling TableFormer) to achieve acceptable cell boundary fidelity (>85%), but running Docling on entire multi-page documents reduces throughput from ~35–45 pages/sec down to ~0.3 pages/sec and causes memory bloat (>4.5 GB RSS).
3. **Whole-Document vs Per-Page Granularity**: In typical documents, only 10–25% of pages contain complex tables. Whole-document routing assigns all pages to the slowest pipeline tier, wasting 75–90% of compute on clean text pages.

---

## 2. Experimental & Production Validation

The 3-tier per-page routing architecture was validated across three rigorous evaluation benchmarks:
1. **Experimental Prototype Campaign**: 2,017 documents (27,914 pages) spanning Corpus B (1,000 docs) and Corpus 945 (945 docs).
2. **Side-by-Side Calibration Benchmark**: Exact 40 calibration documents (20 `curated_hard` + 20 `curated_easy`, 432 pages) evaluated side-by-side using the Gemini LLM Judge (`gemini-3.5-flash-lite`).
3. **100-Document Mixed Corpus Evaluation**: 100 documents (1,302 pages) spanning scanned receipts, invoices, tickets, certificates, dense tables, and clean digital articles.

### Side-by-Side Calibration Scorecard (40 Documents / 432 Pages)

| Dimension / Metric | Pass 1 (0% Docling Native Baseline) | Experimental Prototype (Hybrid Slicing) | Production Pipeline (`smart_routing`) | Empirical Delta / Status |
|---|---|---|---|---|
| **Hard Docs: Table Quality** | 68.1% | **87.7%** | **83.8%** *(93.1% evaluable)* | **+15.7pp to +19.6pp GAIN** |
| **Hard Docs: Structure** | 88.9% | **93.9%** | **95.3%** | **+6.4pp GAIN (EXCEEDS PROTOTYPE)** |
| **Hard Docs: Completeness** | 93.7% | **96.9%** | **97.9%** | **+4.2pp GAIN (EXCEEDS PROTOTYPE)** |
| **Hard Docs: Fidelity** | 93.1% | **96.4%** | **97.8%** | **+4.7pp GAIN (EXCEEDS PROTOTYPE)** |
| **Hard Docs: References** | 87.9% | **91.6%** | **96.7%** | **+8.8pp GAIN (EXCEEDS PROTOTYPE)** |
| **Easy Docs: Completeness** | 98.0% | **98.1%** | **98.7%** | **+0.7pp GAIN** |
| **Easy Docs: Fidelity** | 98.5% | **98.9%** | **98.9%** | **100% FIDELITY PARITY** |
| **Hard Docs: Pass Rate** | 58.0% | **95.0%** (14P / 5PWI / 1F) | **100.0%** (10P / 10PWI / **0 FAIL**) | **100% PASS (0 FAILURES)** |
| **Easy Docs: Pass Rate** | 95.0% | **100.0%** (17P / 3PWI / 0F) | **100.0%** (16P / 4PWI / **0 FAIL**) | **100% CLEAN** |

---

## 3. Why the Difference Between Prototype vs. Production?

### 3.1 Spatial Geometry & Full DOM Validation
- **Experimental Prototype:** Generated lightweight markdown string representations without computing pixel coordinate bounding boxes, reading order directed acyclic graphs (DAGs), or hierarchical block trees.
- **Production Pipeline:** Generates full canonical DOMs with pixel-accurate bounding boxes (`[x0, y0, x1, y1]`), block kind classifications (headings, paragraphs, lists, captions, code), reading order DAGs, and strict JSON Schema validation.

### 3.2 Full Neural TableFormer Cell Snapping
- The production pipeline runs the full PyTorch TableFormer neural model on CPU with worker process isolation (`max_tasks_per_child=10`) for each table-bearing page, recovering complete column/row spans, multi-line headers, and grid topologies. This yields **97.8% Fidelity** and **95.3% Structure** (outperforming the prototype).

### 3.3 Dual-Store Atomic Persistence
- Production commits every page result atomically to the `PageStore`, records immutable audit trails in `plan.json` within the `Ledger`, and enforces strict validation (guaranteeing **0 silent page drops** and **0 dead-letter pages** across all 432 calibration pages and 1,302 mixed corpus pages).

### 3.4 Summary Throughput Acceleration
- Compared to the legacy whole-document Docling approach (**~0.25–0.40 p/s**), the `smart_routing` production pipeline delivers a **~5.5x to 8x overall throughput acceleration (2.04 p/s vs 0.3 p/s)** while preserving **~35–45 p/s** on clean digital pages (86.3% of pages) and achieving **100% PASS** on hard documents.

---

## 4. Architectural Decision & Core Invariants

We adopt a **3-Tier Per-Page Smart Routing Architecture** integrated into the core production pipeline:

### 4.1 Pre-Routing Inspection Tier (`app/routing/inspectors.py`)
- Integrate `firecrawl/pdf-inspector` Rust stream analysis (`process_pdf_bytes`) to perform sub-30ms pre-classification directly on PDF dictionary streams without visual rendering.
- Extract `pages_with_tables`, `pages_with_columns`, `pages_needing_ocr`, and `has_encoding_issues` in Rust.
- **Graceful Fallback**: If `pdf-inspector` is uninstalled or fails on an edge case, `FastInspector` falls back to PyMuPDF `_find_table_presence` and `_est_multi_column` without breaking the `InspectorFeatures` contract.

### 4.2 3-Tier Per-Page Routing Policy (`app/parser/planner.py` & `app/routing/config.py`)
- **Tier 1 (`rust_native` / `native_pdf`):** Clean digital text, multi-column, and list pages route to the fast path (~35–45 pages/sec, peak RAM < 0.15 GB RSS).
- **Tier 2 (`enrichment_ocr` / `cuda_ocr`):** Scanned documents, tickets, receipts, and pages with corrupt CMaps route to RapidOCR (PP-OCRv6 ONNX).
- **Tier 3 (`docling_heavy`):** Pages with detected tables are escalated to single-page Neural TableFormer cell-snapping. Clean pages within the same document remain on Tier 1.

### 4.3 Single-Page TableFormer Execution Slicing (`app/parser/loaders/docling_loader.py`)
- Docling is executed strictly on isolated single-page buffers using `page_range=(page + 1, page + 1)` and PyMuPDF byte slicing (`convert_path` / `parse(page_bytes)`).
- Whole-document Docling calls are strictly prohibited (**0 whole-doc calls invariant**).

### 4.4 Worker Pool Process Recycling (`app/parser/scheduler.py` & `app/parser/config.py`)
- To prevent PyTorch/ONNX C++ heap accumulation (`std::bad_alloc`) during high-scale batch execution (>5,000 pages), `ProcessPoolExecutor` enforces `heavy_pool_max_tasks_per_child = 10`.
- Worker processes restart after 10 page tasks, resetting C++ heap fragmentation while amortizing warm-up overhead.

---

## 5. Invariants & Defensive Fallbacks (Zero-Silent-Loss)

1. **Inspection Invariant**: `FastInspector.inspect()` returns `InspectorFeatures` or `None`. Missing features remain `None` / empty (never fabricated false negatives).
2. **Engine Invariant**: If Docling model weights or dependencies are unavailable (`engine_available() == False`), table pages gracefully downgrade to `enrichment` or `native` with zero dropped pages.
3. **Execution Containment**: If Docling throws an exception or encounters memory pressure during single-page inference, the error is caught at the page boundary and immediately falls back to native text extraction. The page is never lost.
4. **Deterministic Auditing**: All routing signals, detector versions, and confidence scores are stamped onto `RoutingDecision` for end-to-end provenance.

---

## 6. Status & Traceability

- **Implementation Branch:** `smart_routing`
- **Experimental Code & Calibration Harness:** `experimental/pdf_inspector_eval/`
- **Calibration Comparison Report:** `artifacts/calibration_comparison_report.md` & `artifacts/calibration_comparison_report.json`
- **Mixed 100 Corpus Report:** `artifacts/mixed_100_evaluation_report.md` & `artifacts/mixed_100_evaluation_report.json`
- **Experimentation Plan & Benchmark Run Log:** `docs/pdf-inspector-experimentation-plan.md`
