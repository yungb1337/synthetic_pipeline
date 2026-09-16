# Experimental: Baidu Unlimited-OCR Adapter & Evaluation Harness

This directory contains the **isolated experimental evaluation path** for evaluating **Baidu Unlimited-OCR (PP-OCRv6)** as a candidate specialist/raster OCR parser in comparison to the existing Docling/Native parsing pipeline.

---

## 1. System Architecture & Boundaries

```
                    Source Document (PDF)
                              │
            ┌─────────────────┴─────────────────┐
            ▼                                   ▼
    [Existing Pipeline]             [Unlimited-OCR Adapter]
  (Native / Docling Engine)             (Local PP-OCRv6)
            │                                   │
            ▼                                   ▼
    [Canonical DOM JSON]              [Raw OCR BBoxes/Text]
            │                                   │
            │                         [Unlimited-OCR Converter]
            │                                   │
            │                         [Canonical DOM + Normalizer]
            │                                   │
            └─────────────────┬─────────────────┘
                              ▼
                 [Existing Gemini LLM Judge]
            (Same prompts, metrics & schema)
                              │
                              ▼
                 [Multi-Metric Comparative Report]
```

### Critical Isolation Rules
- **No Production Modifications:** Does not alter `app/routing/`, `app/parser/`, or existing parser policies.
- **Identical Corpus:** Evaluates on the exact same benchmark corpora used by the baseline pipeline.
- **Immutable Raw Artifacts:** Persists all raw OCR boxes, text, timings, and metadata under `artifacts/unlimited_ocr_eval/`.
- **Identical Judge Methodology:** Reuses `scripts/llm_judge.py` with zero modifications to prompt templates or metric scoring dimensions.

---

## 2. Model & Runtime Specifications

| Attribute | Specification |
|---|---|
| **Engine** | Baidu PP-OCRv6 (via `rapidocr` ONNXRuntime backend) |
| **Detection Model** | `PP-OCRv6_det_small.onnx` (~4.5 MB) |
| **Direction Classifier** | `ch_ppocr_mobile_v2.0_cls_mobile.onnx` (~1.4 MB) |
| **Recognition Model** | `PP-OCRv6_rec_small.onnx` (~10.8 MB) |
| **Total Parameter Footprint** | ~16.7 MB |
| **Inference Backend** | ONNX Runtime (CPU multi-threaded, zero-VRAM footprint) |
| **Rendering Backend** | PyMuPDF (`fitz`) at 150 DPI with `OCR_MAX_EDGE=2000` memory guard |
| **Python Version** | 3.14.6 64-bit |
| **Judge Model** | `gemini-3.5-flash-lite` (with fallback to `gemini-3.1-flash-lite`, `gemini-3.5-flash`) |

---

## 3. Directory Layout

```
experimental/unlimited_ocr/
├── __init__.py
├── config.py           # Configuration parameters and thresholds
├── adapter.py          # PDF rendering, PP-OCR inference, error and repetition detection
├── converter.py        # Traceable mapping from OCR coordinates to Canonical Document DOM
├── metrics.py          # Deterministic structural metrics and repetition scoring
├── judge_evaluator.py  # Gemini LLM Judge wrapper reusing scripts/llm_judge.py
├── artifacts.py        # Isolated storage manager for artifacts and logs
├── runner.py           # Multi-experiment runner (Exp A, Exp B, Exp C, Report)
├── cli.py              # Turnkey command line runner
└── README.md           # Architecture and reproducibility documentation
```

---

## 4. How to Run the Evaluation

### Run Quick Evaluation (6 Reference Documents)
```bash
.venv/Scripts/python.exe -m experimental.unlimited_ocr.cli --corpus reference --repeat 2
```

### Run Full Benchmark on Reliability Corpus
```bash
.venv/Scripts/python.exe -m experimental.unlimited_ocr.cli --corpus reliability --limit 20 --repeat 3
```

### Run Specific Document or Folder
```bash
.venv/Scripts/python.exe -m experimental.unlimited_ocr.cli --corpus checkpoints/run/run-2026-09-03-llm-judge-test/sources/PMC10875432.pdf
```

---

## 5. Artifacts and Output Layout

All artifacts are written to isolated locations:

- **Document Artifacts:** `artifacts/unlimited_ocr_eval/<document_id>/`
  - `source_metadata.json` — Input file hash, size, and source metadata
  - `raw_output.json` — Raw bounding boxes, scores, and unformatted lines
  - `raw_output.md` — Assembled Markdown text
  - `normalized_output.json` — Canonical Document JSON (v0.1.0)
  - `metrics.json` — Structural and performance metrics
  - `runtime.json` — Execution times, page counts, and error tallies
  - `logs/stdout.log` & `logs/stderr.log` — Raw captured streams
- **Evaluation Summary:** `evaluation/unlimited_ocr/`
  - `manifest.json` — Evaluated document manifest
  - `results.json` — Per-document results and performance records
  - `judge_results.json` — Side-by-side LLM Judge verdicts and issues
  - `report.json` — Aggregated metrics and summary data
- **Markdown Report:** `docs/unlimited-ocr-evaluation.md`
