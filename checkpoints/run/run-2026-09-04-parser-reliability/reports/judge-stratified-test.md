# Stratified LLM Judge Audit — 120-Document Benchmark

- Generated: `2026-09-07T17:29:34Z`
- Target sample: `120` documents (`24` per stratum across `S1..S5`)
- Evaluated: `5` documents (newly judged: `5`, cached: `0`)
- Judge Model: `gemini-3.5-flash-lite`
- Evaluation Harness: Multi-metric source-vs-DOM correspondence (`scripts/llm_judge.py`)

## 1. Executive Summary & Verdict Distribution

| Verdict | Count | Share | Description |
|---|---|---|---|
| **PASS** | `4` | 80.0% | Complete, high-fidelity DOM extraction with zero critical/major defects |
| **PASS_WITH_ISSUES** | `1` | 20.0% | High-fidelity extraction with minor structural / formatting nuances |
| **FAIL** | `0` | 0.0% | Major extraction defect / substantial content loss |
| **Total** | `5` | 100.0% | **Acceptance Rate (PASS + PASS_WITH_ISSUES): 100.0%** |

## 2. Multi-Metric Accuracy by Risk Stratum

| Stratum | Description | Docs | Completeness | Fidelity | Structure | Tables | References | Scans/OCR | Verdict (P / PWI / F) |
|---|---|---|---|---|---|---|---|---|---|
| `S1` | Dense Tables & Multi-Table Studies | `1` | `0.980` | `0.990` | `0.950` | `0.000` | `0.950` | `1.000` | 1 / 0 / 0 |
| `S2` | Multi-Column Layouts & Typography | `1` | `0.980` | `0.990` | `0.970` | `0.950` | `0.980` | `1.000` | 1 / 0 / 0 |
| `S3` | OCR / Scans & Legacy Literature | `1` | `0.980` | `0.970` | `0.950` | `0.950` | `0.950` | `1.000` | 0 / 1 / 0 |
| `S4` | Long Documents (>30 pages) & Guidelines | `1` | `1.000` | `1.000` | `1.000` | `0.000` | `1.000` | `1.000` | 1 / 0 / 0 |
| `S5` | Clinical Trial Reports & Structured Outcomes | `1` | `0.980` | `0.990` | `0.950` | `1.000` | `0.950` | `1.000` | 1 / 0 / 0 |
| **Overall** | **Full Stratified Corpus** | `5` | **`0.984`** | **`0.988`** | **`0.964`** | **`0.580`** | **`0.966`** | **`1.000`** | **4 / 1 / 0** |

## 3. Issues & Nuances Tally

- **Total Issues Identified:** `2`
  - Critical: `0`
  - Major: `0`
  - Minor: `2`

### Issues by Surface

| Surface | Total Issues | Critical | Major | Minor | Sample Feedback / Suggestion |
|---|---|---|---|---|---|
| `table` | `2` | `0` | `0` | `2` | Refine table cell boundary detection to ensure cleaner text extraction.... |

## 4. Per-Document Audit Log (Stratified Sample)

| # | Doc ID | PMC ID | Stratum | Verdict | Completeness | Fidelity | Structure | Tables | Refs | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `d-20f3a9f24da77e3f` | `PMC13358229` | `S1` | `PASS` | 0.98 | 0.99 | 0.95 | 0.00 | 0.95 | The parsed DOM corresponds very closely to the source PDF across all sampled pages. Tex... |
| 2 | `d-b91658b49d30e297` | `PMC13235863` | `S2` | `PASS` | 0.98 | 0.99 | 0.97 | 0.95 | 0.98 | The DOM successfully parses all the primary text, metadata, figures, and structural ele... |
| 3 | `d-720fdc00ea191545` | `PMC13366240` | `S3` | `PASS_WITH_ISSUES` | 0.98 | 0.97 | 0.95 | 0.95 | 0.95 | The DOM successfully captures the document text, layout, and structured tables with hig... |
| 4 | `d-f844aa5fd9c37984` | `PMC13102093` | `S4` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The automated parser successfully extracted all text, figures, and metadata from the do... |
| 5 | `d-090c472e097ddff1` | `PMC13324859` | `S5` | `PASS` | 0.98 | 0.99 | 0.95 | 1.00 | 0.95 | The automated parser successfully extracted the text, metadata, and structural elements... |