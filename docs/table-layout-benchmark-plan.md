# Systematic Table + Layout Model Benchmark Plan (MedFactory AI)

**Date:** 2026-09-16  
**Objective:** Establish a composable, modular benchmark across zero-model, lightweight neural (TATR, SLANet), Docling components, and hybrid cascades to find the optimal combination of Layout Detection + Table Detection + Table Structure Recognition + OCR that maximizes document fidelity and table structure while staying safely within RTX 3050 4GB VRAM and achieving high throughput (>0.3–0.5 pages/sec).

---

## 1. Existing Components Discovered & Audited

1. **OCR Engine:**
   - RapidOCR (PP-OCRv6) with PyTorch CUDA execution engine (`app/embedding/` & `experimental/unlimited_ocr/adapter.py`). Peak VRAM ~350MB, deterministic, high text fidelity.
2. **Canonical DOM Architecture:**
   - `app/parser/dom/models.py`: `Document`, `Page`, `Table`, `Row`, `Cell`, `BBox`, `Block`, `Reference`, `ReadingOrderEntry`.
   - `Table` schema supports `bbox`, `header: list[str]`, `rows: list[Row]`, `caption: str`, `source: str`, `confidence: float`.
   - `Row` carries `cells: list[Cell]` and optional `bbox`.
   - `Cell` carries `text: str` and `bbox: Optional[BBox]`.
3. **Existing Normalizer:**
   - `app/normalizer/normalizer.py`: Clean DOM pipeline (whitespace standardization, reference structuring, reading order validation).
4. **Existing LLM Judge:**
   - `scripts/llm_judge.py`: `gemini-3.5-flash-lite`, 6 dimensions (`completeness`, `fidelity`, `structure`, `tables`, `references`, `scans_ocr`). Summarizes DOM with bounded table preview.
5. **Reference Benchmark Corpus:**
   - `checkpoints/run/run-2026-09-03-llm-judge-test/sources/` (6 reference multi-page and single-page medical/scientific PDFs with known baselines).
   - `checkpoints/run/e2e-10-doc-verification/input_pdfs/` (10 curated diverse PDFs).
6. **Hardware & Environment:**
   - OS: Windows 11 Home Single Language.
   - GPU: NVIDIA GeForce RTX 3050 Laptop GPU (4.0 GB VRAM total).
   - CUDA: 12.6, PyTorch: 2.13.0+cu126.
   - Python: 3.14 (.venv).

---

## 2. Models Already Available & Cached Locally

| Model | Location / Source | Weights Size | Primary Purpose | Cached Status |
| :--- | :--- | :--- | :--- | :--- |
| **PP-OCRv6** | RapidOCR PyTorch backend | ~15 MB | Text & OCR line detection/recognition | Available |
| **Docling Heron Layout** | `~/.cache/huggingface/hub/models--docling-project--docling-layout-heron` | 171.7 MB | Document layout parsing | Cached locally |
| **Docling TableFormer FAST** | `~/.cache/huggingface/hub/models--docling-project--docling-models/tableformer_fast.safetensors` | 145.5 MB | Fast table structure recognition | Cached locally |
| **Docling TableFormer ACCURATE** | `~/.cache/huggingface/hub/models--docling-project--docling-models/tableformer_accurate.safetensors` | 212.8 MB | Accurate table structure recognition | Cached locally |
| **BGE-M3** | `~/.cache/huggingface/hub/models--BAAI--bge-m3` | ~2.2 GB | Text embedding seam | Cached locally |

---

## 3. Models & Packages to Investigate / Install

1. **Zero-Model Table Extractors:**
   - `pdfplumber`: lightweight Python PDF parsing with explicit visual line/curve & whitespace heuristics.
   - `PyMuPDF` (`fitz.page.find_tables()`): natively installed, supports `lines`, `text`, `explicit`, snapping, tolerances.
   - `camelot-py`: investigate if lattice/stream extraction is installable without breaking Windows dependencies.
2. **Lightweight Neural Table Models (<= 500MB):**
   - `microsoft/table-transformer-detection` (~115 MB): DETR-based table detection.
   - `microsoft/table-transformer-structure-recognition` (~115 MB): DETR-based TSR (rows, columns, headers, spans).
   - `timm` / `transformers`: HuggingFace model dependencies.
   - `RapidStructure` / `SLANet` (Baidu PP-Structure): ONNX/PyTorch lightweight structure model (~15-30 MB weights).
3. **Lightweight Layout Detectors:**
   - Docling Heron (extracted as standalone crop layout detector).
   - Lightweight YOLO / PicoDet table bounding box detectors.

---

## 4. Candidate Permutation Matrix

| ID | Layout Detector | Table Detector | Table Structure Recognizer (TSR) | OCR Backend | Description / Strategy |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **P000** | Docling Heron | Docling | Docling TableFormer CPU | Native/Docling | Current heavy baseline comparison |
| **P001** | None | None | None | RapidOCR (PP-OCRv6) | Previous unlimited-ocr baseline (Table score ~0.28) |
| **P002** | None | PyMuPDF (lines) | PyMuPDF (lines) | PP-OCRv6 | Fast rule-based table extraction on grid lines |
| **P003** | None | PyMuPDF (hybrid) | PyMuPDF (lines+text) | PP-OCRv6 | Multi-strategy PyMuPDF with snapping & whitespace |
| **P004** | None | pdfplumber (lines) | pdfplumber (lines) | PP-OCRv6 | Visual vector line & rectangle extractor |
| **P005** | None | pdfplumber (text) | pdfplumber (text-tolerance) | PP-OCRv6 | Text-alignment / whitespace-based extractor |
| **P006** | None | Camelot (lattice/stream) | Camelot | PP-OCRv6 | Camelot table extractor (if environment allows) |
| **P007** | None | HF TATR-Detection | HF TATR-Structure | PP-OCRv6 | Full-page Microsoft Table Transformer pipeline |
| **P008** | None | PyMuPDF (bbox finder) | HF TATR-Structure (Crop) | PP-OCRv6 | **Crop-only TATR**: Cheap detector finds bbox, TATR runs only on cropped table sub-image |
| **P009** | Docling Heron | Heron Layout | HF TATR-Structure (Crop) | PP-OCRv6 | Layout model crops table -> TATR structures -> PP-OCRv6 fills cells |
| **P010** | None | PyMuPDF (bbox finder) | SLANet / PP-Structure | PP-OCRv6 | SLANet on cropped table regions |
| **P011** | Docling Heron | Heron Layout | SLANet / PP-Structure | PP-OCRv6 | Heron layout + SLANet structure recognition |
| **P012** | Docling Heron | Heron Layout | Docling TableFormer FAST (Crop) | PP-OCRv6 | Isolated TableFormer FAST on sub-image crop |
| **P013** | Docling Heron | Heron Layout | Docling TableFormer ACCURATE (Crop)| PP-OCRv6 | Isolated TableFormer ACCURATE on sub-image crop |
| **P014** | None (Hybrid) | Hybrid Rule/Neural | Hybrid (PyMuPDF -> TATR) | PP-OCRv6 | **H1 Cascade**: Digital vector lines -> PyMuPDF; Borderless/complex -> TATR crop |
| **P015** | Geometry Grid | Adaptive Grid BBox | Adaptive Crop TSR | PP-OCRv6 | **H2 Confidence Cascade**: Fast detection confidence gate |
| **P016** | Multi-Band | Page Complexity Router | Tiered TSR (PyMuPDF / TATR / Docling) | PP-OCRv6 | **H3 Tri-Band Router**: Low-complexity -> PyMuPDF; Medium -> TATR; Ultra -> Docling CPU |

---

## 5. Expected Compatibility & Resource Analysis

| Component | Weight Size | Est. Runtime VRAM (Crop) | Est. Latency / Page | 4GB GPU Safe? |
| :--- | :--- | :--- | :--- | :--- |
| **PP-OCRv6 (RapidOCR)** | 15 MB | ~350 MB | ~150–250 ms | Yes (Substantial headroom) |
| **PyMuPDF find_tables()** | 0 MB (CPU) | 0 MB | ~5–15 ms | Yes (100% safe) |
| **pdfplumber** | 0 MB (CPU) | 0 MB | ~20–50 ms | Yes (100% safe) |
| **HF Table Transformer (TATR)** | ~115 MB | ~400–600 MB | ~200–400 ms (per table crop) | Yes (Combined ~850MB VRAM) |
| **SLANet / PP-Structure** | ~20 MB | ~200–350 MB | ~100–200 ms (per table crop) | Yes (Combined ~600MB VRAM) |
| **Docling TableFormer (Crop)** | 145–212 MB | ~1.2–1.8 GB | ~800–1500 ms | Likely safe if run on crop only; test required |
| **Full Docling Pipeline** | >500 MB | >4.0 GB (OOM) | >6000 ms (CPU fallback) | Unsafe for GPU, must use CPU |

---

## 6. Benchmark Stages

```text
[Stage 1: Smoke Test]
  - 1–3 representative PDFs (e.g. 2302.04143, PMC10234567)
  - Verify initialization, memory allocation, table extraction, DOM conversion, crash safety.
  - 0 LLM API calls.
       │
       ▼
[Stage 2: Performance & GPU Safety Screening]
  - 10-document reference corpus
  - Measure: Peak VRAM, Peak RAM, ms/page, tables extracted, malformed rows.
  - Eliminate: OOM configs, crashes, slow models (>2.5s/page), zero-table output.
  - 0 LLM API calls.
       │
       ▼
[Stage 3: Quality Evaluation via Existing LLM Judge]
  - Run surviving candidate DOMs through scripts/llm_judge.py with gemini-3.5-flash-lite.
  - Record: Completeness, Fidelity, Structure, Tables, References, Scans/OCR.
  - Evaluate table-specific score vs 0.97 baseline.
       │
       ▼
[Stage 4: Pareto Analysis & Repeatability Validation]
  - Re-run top 3–4 candidates on expanded test sets to verify variance and deterministic stability.
       │
       ▼
[Stage 5: Final Evaluation Report]
  - Generate docs/table-layout-benchmark.md with comprehensive Pareto rankings, hardware profiles, failure analysis, and next steps.
```

---

## 7. Hardware & VRAM Profiling Methodology

A strict `HardwareProfiler` context manager will wrap all execution units:
1. **GPU Isolation:**
   - Clear PyTorch CUDA cache via `torch.cuda.empty_cache()` and `gc.collect()`.
   - Call `torch.cuda.reset_peak_memory_stats()`.
   - Sample `torch.cuda.memory_allocated()` and `torch.cuda.max_memory_allocated()`.
   - Sample `torch.cuda.memory_reserved()` and `torch.cuda.max_memory_reserved()`.
2. **Host RAM & CPU Profiling:**
   - Sample process RSS memory via `psutil.Process().memory_info().rss`.
3. **High-Resolution Stage Timing:**
   - High-precision `time.perf_counter()` broken down into:
     - `init_ms`: Model loading / warm-up.
     - `preprocess_ms`: PDF rasterization / image cropping.
     - `inference_ms`: Neural forward passes or rule detection.
     - `postprocess_ms`: Cell matching, coordinate remapping, table reconstruction.
     - `dom_conversion_ms`: DOM assembly + Normalizer execution.
4. **Safety Margin:** Any run exceeding 3.2 GB peak VRAM on the 4.0 GB RTX 3050 is flagged as an unsafe configuration.

---

## 8. Table Output to Canonical DOM Mapping

1. **Table Bounding Box:** Bounding box $[x_0, y_0, x_1, y_1]$ in PDF page space.
2. **Cell & Grid Extraction:**
   - Model detects cell bounding boxes with row index $r$, column index $c$, $row\_span$, and $col\_span$.
3. **Cell Text Assignment:**
   - Digital PDFs: PyMuPDF character/word intersection within cell bbox.
   - OCR PDFs: Spatial matching of RapidOCR line/word bounding boxes against cell bounding boxes using center-in-box and IoA (Intersection-over-Area $> 0.5$).
4. **Paragraph Occlusion / De-duplication:**
   - **Crucial Rule:** Any OCR text block whose bounding box falls inside a detected `Table` bounding box is suppressed from generic `page.blocks` to prevent duplicated text and reading order corruption.
5. **Canonical DOM Assembly:**
   - Build `Table(id=f"t-p{page}-{idx}", page=page, bbox=..., header=..., rows=[Row(cells=[Cell(...)])], source=...)`.
   - Populate `page.tables.append(table)`.
   - Append `ReadingOrderEntry(type="table", id=table.id)` to `Document.reading_order_full`.
6. **Normalization:** Pass output DOM through `Normalizer().normalize(doc)`.

---

## 9. Reuse of Existing LLM Judge

- Direct reuse of `scripts/llm_judge.py` using identical prompt template and scoring criteria.
- Target Model: `gemini-3.5-flash-lite` (with rate-limiting pacing and exponential backoff).
- Output Schema:
  ```json
  {
    "verdict": "PASS | PASS_WITH_ISSUES | FAIL",
    "metrics": {
      "completeness": 0.0,
      "fidelity": 0.0,
      "structure": 0.0,
      "tables": 0.0,
      "references": 0.0,
      "scans_ocr": 0.0
    },
    "issues": []
  }
  ```
- No modification of existing judge formulas or weighting.

---

## 10. File Structure & Implementation Plan

```text
experimental/table_eval/
├── __init__.py
├── config.py                  # Configurations for permutations P000-P016
├── profiler.py                # Hardware & VRAM telemetry tracking
├── adapters/
│   ├── __init__.py
│   ├── base.py                # Base TableDetector, LayoutDetector, TSR interfaces
│   ├── pymupdf_adapter.py     # PyMuPDF line/hybrid table extraction
│   ├── pdfplumber_adapter.py  # pdfplumber line/text table extraction
│   ├── tatr_adapter.py        # HF Table Transformer detection & structure recognition
│   ├── slanet_adapter.py      # SLANet / PP-Structure extractor
│   └── docling_adapter.py     # Standalone Docling Heron & TableFormer crop extraction
├── cell_matcher.py            # Spatial OCR/text to table cell bounding box assignment
├── converter.py               # Raw table + OCR outputs -> canonical Document DOM
├── strategies.py              # Composable strategy pipelines & hybrid cascades
├── runner.py                  # Batch execution engine across stages
├── judge_evaluator.py         # Interfaces directly with scripts/llm_judge.py
├── artifacts.py               # Manages evaluation/table_benchmark/ & artifacts/table_eval/
├── cli.py                     # Experimental CLI
└── README.md
```

- **Output Artifacts:**
  - `artifacts/table_eval/Pxxx/` (Raw tables, normalized DOMs, telemetry JSONs)
  - `evaluation/table_benchmark/` (`manifest.json`, `results.json`, `judge_results.json`, `failures.json`)
  - `docs/table-layout-benchmark.md` (Final benchmark report)

---

## 11. Explicit Constraints (What Will NOT Be Changed)

1. `app/parser/` production code remains completely untouched.
2. `app/routing/` production routing policy remains completely untouched.
3. `app/normalizer/` logic is reused as an immutable module.
4. `scripts/llm_judge.py` scoring criteria and prompts remain untouched.
5. Existing test cases and evaluation artifacts in `evaluation/unlimited_ocr/` and `checkpoints/` will not be overwritten.
6. The winning strategy will NOT be merged into production during this benchmark phase.

---

## 12. Estimated Number of Experiments

- **17 distinct permutations** (P000 to P016).
- Progressive filtering will evaluate:
  - 17 permutations in Stage 1 (Smoke test on 2 docs).
  - 12–14 surviving permutations in Stage 2 (Performance screening on 10 docs).
  - 5–7 top candidate permutations in Stage 3 (LLM Judge evaluation).
  - Top 3 finalists in Stage 4 (Repeatability & deep dive).

---

## 13. Elimination Criteria

1. **VRAM Violation:** Peak VRAM > 3.2 GB on RTX 3050 (leaving < 800MB headroom), or any CUDA OOM.
2. **Throughput Floor:** Throughput < 0.20 pages/sec (> 5000 ms/page) without massive quality advantage.
3. **Table Structure Failure:** Table judge score < 0.35 on table documents, or > 15% missing/collapsed rows.
4. **Text / Reading Order Regression:** Completeness or fidelity drops by > 0.15 compared to baseline due to text occlusion or duplicate extraction.
5. **Stability / Crash:** Any unhandled exception, segfault, or non-deterministic behavior.
