# Corpus 945 Benchmark Report: P003 Hybrid vs Historical Baseline

**Date:** 2026-09-17  
**Strategy Tested:** `P003` (PyMuPDF Hybrid Table Extraction + RapidOCR GPU + OCR Occlusion Suppression)  
**Dataset:** Corpus 945 (1 PMC Open Access Documents, 19 Pages)  
**LLM Judge:** Google Gemini 3.5 Flash Lite (1 Documents Judged)  

## 1. Executive Summary & Paired Scorecard

| Metric / Dimension | P003 Engine | Historical Baseline | Absolute Delta | Relative Gain |
| :--- | :--- | :--- | :--- | :--- |
| **Tables** | **0.00%** | 100.00% | **-100.00%** | **-100.0%** |
| **Structure** | **40.00%** | 100.00% | **-60.00%** | **-60.0%** |
| **Completeness** | **60.00%** | 100.00% | **-40.00%** | **-40.0%** |
| **Fidelity** | **50.00%** | 100.00% | **-50.00%** | **-50.0%** |
| **References** | **30.00%** | 100.00% | **-70.00%** | **-70.0%** |
| **Scans Ocr** | **80.00%** | 100.00% | **-20.00%** | **-20.0%** |

## 2. Verdict Distribution Comparison

| Verdict Status | P003 (N=1) | Baseline (N=1) |
| :--- | :--- | :--- |
| **PASS (Clean)** | **0 (0.0%)** | 1 (100.0%) |
| **PASS_WITH_ISSUES** | **0 (0.0%)** | 0 (0.0%) |
| **FAIL** | **1 (100.0%)** | 0 (0.0%) |

## 3. Hardware & Throughput Profile

- **Total Documents Processed:** 1
- **Total Pages Parsed:** 19
- **Total Tables Extracted:** 0
- **Failures / Unparsed:** 0
- **Mean Page Latency:** 1909.23 ms/page
- **Effective Throughput:** 0.524 pages/sec
- **Peak Host RAM:** 1532.8 MB RSS
- **Peak GPU VRAM:** 1328.0 MB