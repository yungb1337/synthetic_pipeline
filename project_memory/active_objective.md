---
name: active-objective
description: The run brief — the objective and notes the autonomous organization executes next. Overwrite the body for each new run; keep this file.
metadata:
  type: project
---

# Active Run Brief

> The Project Orchestrator (`/dev-team` in-session, or the `audit` workflow in background)
> reads this file at the start of a run. Replace the contents for a new run; keep this file.

## Run id
`run-2026-08-20-extraction-quality`

## Objective
Determine why the current canonical DOM loses or weakens document structure even though ordinary text extraction is strong, and fix the underlying pipeline so the DOM is a faithful, loss-minimizing representation suitable for downstream GenAI, RAG, chunking, retrieval, and knowledge extraction.

The test case is:
* Source PDF: "C:\Users\Asus\Downloads\test_cases_output\raw\0edc810eb07d15e917ae69d6324e6407e81e0f962c741c8176110246de59691e.pdf"
* Generated DOM: `dom-v0.1.0.docJSON` at "C:\Users\Asus\Downloads\test_cases_output\dom\d-0edc810eb07d15e9\dom-v0.1.0.docJSON"
* Parser version: `parser-v0.1.0`
* DOM schema: `dom-schema-v0.1.0`
* Docling version: `2.118.0`

The document was routed to the Docling path with complexity score `83`. Inspection signals include:
* multi-column probability: `1.00`
* layout complexity: `0.925`
* reading-order ambiguity: `0.827`
* font diversity: `1.00`

This is a high-complexity document and should be treated as an important structural extraction test case.

## Critical Constraint
Docling is the table extractor/layout engine responsible for the table structures in this case. When investigating table failures, trace the complete path:
PDF → Docling → extracted table structure → parser adapter/normalization → canonical DOM

Determine exactly where the structural degradation happens.

## Investigation Scope (per user directive)
1. **Source-vs-DOM ground truth** — systematic comparison at document/page/text/block/heading/list/table/image/reading-order/reference/metadata levels
2. **Text extraction fidelity** — quantitative metrics: char/token recall, missing/duplicated/reordered/modified classification
3. **Page ordering** — verify whether page 8 appears after page 24 in serialized DOM
4. **Reading order** — verify every content-bearing block appears exactly once; tables/images/captions/footnotes/references in reading order; multi-column handling; determinism
5. **Table extraction (highest priority)** — trace Table 1, Table 5, Table 6 through Docling → adapter → DOM; determine where rows collapse
6. **Docling raw output inspection** — compare PDF visual table → Docling table → adapter → canonical DOM at all four stages
7. **Table structural recoverability** — downstream consumer must reconstruct row/column semantics, not concatenated mega-cells
8. **References/bibliography** — currently `references: []` but citations like [33], [37] present in body; investigate extraction, numbering, schema support
9. **Image handling** — detection, asset preservation, caption association, reading-order placement
10. **Metadata consistency** — page with missing/null dimensions (page 24)
11. **Duplication/omission accounting** — document-level content accounting
12. **Content vs structural fidelity** — separate dimensions

## Deliverable
Before implementation, produce an investigation report containing:
1. Executive summary
2. Source document characteristics
3. Text fidelity metrics
4. Page-order analysis
5. Reading-order analysis
6. Table-by-table analysis
7. Docling raw-output analysis
8. Adapter/normalization analysis
9. Reference/bibliography analysis
10. Image analysis
11. Metadata analysis
12. Root-cause matrix
13. Severity classification (P0/P1/P2/P3)
14. Recommended fixes
15. Validation plan

## Implementation Requirements (after investigation)
- Fix root causes, keep existing architecture where sound
- No document-specific hardcoding (no `if document_id == ...`, `if page == 8`, `if table == Table 5`)
- Generic structural reconstruction
- Preserve backward compatibility where practical
- Keep provenance intact
- Regression tests required

## Validation Requirements
After implementation, rerun exact source PDF through real production pipeline:
A. Regression validation (page count, ordering, block/table/image counts, captions, references, reading order)
B. Source-vs-DOM comparison (recompute fidelity metrics)
C. Table validation (source rows = Docling rows = canonical DOM rows)
D. Reading-order validation (every semantic unit exactly once)
E. Reference validation (bibliography entries preserve labels, structurally addressable)
F. No-regression validation (ordinary prose quality maintained)

## Constraints
- Follow `docs/org-gate-protocol.md` hard gates
- Do not modify source PDF
- Tests are part of done: `.venv/Scripts/python.exe -m pytest tests/ -q`