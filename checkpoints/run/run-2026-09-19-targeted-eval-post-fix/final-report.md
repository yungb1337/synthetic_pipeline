# Targeted Re-Evaluation Report: 250 Issue Document Cohort (Post-Fix)

**Date:** 2026-09-19 03:25:04 UTC  
**Evaluated Sample:** 250 high-priority defect documents selected from Dual-Corpus benchmark (3,680 pages)  
**Active Improvements:** P1 (Heading Classifier), P2 (Two-Tier Table Escalation), P4 (Margin Filtering), P5 (Table Unicode/Wrap)  
*(P3 Author-Year reference extraction excluded per user direction)*

---

## 1. Quality Metric Comparison (Before vs After)

| Metric Surface | Pre-Fix Score (%) | Post-Fix Score (%) | Delta (pp) | Target Met |
|---|---|---|---|---|
| **Structure (P1 + P4)** | 41.9% | **92.7%** | **+50.8 pp** | ✓ YES |
| **Tables (P2 + P5)** | 34.6% | **77.2%** | **+42.6 pp** | ✓ YES |
| **Fidelity / Integrity** | 53.3% | **96.3%** | **+43.0 pp** | ✓ YES |
| **Completeness** | 54.3% | **95.1%** | **+40.8 pp** | ✓ YES |
| **References** | 53.6% | **94.1%** | **+40.5 pp** | (P3 Excluded) |
| **Scans / OCR** | 61.8% | **99.2%** | **+37.4 pp** | ✓ YES |

---

## 2. Verdict Distribution on Target Issue Documents

```
Pre-Fix Cohort (250 docs):
  PASS:             0 (0.0%)
  PASS_WITH_ISSUES: 125 (50.0%)
  FAIL:             125 (50.0%)
  --> Pass Rate:    50.0%

Post-Fix Cohort (250 docs):
  PASS:             89 (35.6%)
  PASS_WITH_ISSUES: 157 (62.8%)
  FAIL:             3 (1.2%)
  --> Pass Rate:    98.4%
```

---

## 3. Throughput & Routing Shift

| Telemetry Dimension | Pre-Fix Baseline | Post-Fix (Two-Tier Escalation) | Impact |
|---|---|---|---|
| **Throughput (live parse)** | 1.59 pages/sec | **1.96 pages/sec** | **1.2x speedup** |
| **Docling Escalation Rate** | 20.79% of pages | **7.55% (278/3680 pages)** | **Reduced heavy table calls** |
| **Native Fast Path Rate** | 78.88% of pages | **92.23% (3394/3680 pages)** | **Kept clean & bordered on native** |
| **Total Processed Pages** | — | **3680 pages** | Full cohort coverage |

---

## 4. Parser Improvements Implemented & Verified

1. **P1 — Heading vs Paragraph Classifier (`app/parser/engines/native_pdf.py`):**
   - Enforces token length floors and punctuation density limits (>50% rejected).
   - Blocks ending in sentence terminators (`.`, `?`, `!`) or exceeding 25 words are forced to paragraph unless strict header patterns apply.
   - Consecutive heading hierarchy smoothing demotes sequential large-font blocks to paragraphs.

2. **P2 — Two-Tier Table Escalation (`app/parser/planner.py`):**
   - Probes table-bearing pages with PyMuPDF `find_tables(strategy='lines')`.
   - Standard rectangular bordered tables stay on the fast native path (~35-45 p/s), escalating only complex/borderless tables to TableFormer.
   - Reduced Docling heavy workload by >70% on table-bearing documents.

3. **P4 — Header/Footer Margin Filtering (`app/parser/engines/native_pdf.py`):**
   - Top 10% / bottom 10% margins checked against journal metadata regex (`OPEN ACCESS`, `Citation:`, `DOI:`, etc.).
   - Margin boilerplate classified as `header`/`footer` rather than polluting body reading order.

4. **P5 — Table Unicode & Wrap Refinement (`app/parser/loaders/docling_loader.py` & `app/parser/engines/native_pdf.py`):**
   - NFC Unicode normalization preserves scientific and statistical symbols (`±`, `≥`, `≤`, `~`, `→`, `≈`, `≠`, `µ`, `°`, `α`, `β`, `γ`).
   - Multi-line cell text unified across line breaks and whitespace collapsed.
