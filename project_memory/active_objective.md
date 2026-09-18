---
name: active-objective
description: The run brief — the objective and notes the autonomous organization executes next. Overwrite the body for each new run; keep this file.
metadata:
  type: project
---

# Active Run Brief: `firecrawl/pdf-inspector` Smart Routing Production Promotion

**Run ID:** `run-2026-09-18-smart-routing-prod`  
**Working Branch:** `smart_routing`  
**Prior Runs:** `run-2026-09-17-pdf-inspector-eval` (experimental calibration on 2,017 documents), `run-2026-09-14-eval-1000` (1000-doc baseline), `run-2026-09-14-full-corpus` (945-doc calibrated routing baseline).  
**ADR:** `docs/adr/001-pdf-inspector-smart-routing.md`  
**Experimentation Doc:** `docs/pdf-inspector-experimentation-plan.md`  

## 1. Objective
Promote the validated 3-tier per-page smart routing architecture from experimental evaluation (`experimental/pdf_inspector_eval/`) into the core production pipeline (`app/routing/`, `app/parser/`), delivering:
1. Sub-30ms Rust stream inspection with CMap health check and structural layout detection in `FastInspector` (`app/routing/inspectors.py`).
2. Per-page Docling TableFormer escalation for isolated table pages (`app/parser/planner.py`), unlocking +19.6pp table quality gain (68.1% → 87.7%) while preserving ~35–45 p/s native speed on digital text.
3. ProcessPoolExecutor worker recycling (`heavy_pool_max_tasks_per_child = 10`) in `app/parser/scheduler.py` to prevent C++ memory accumulation over arbitrary corpus sizes.
4. Formal ADR documentation in `docs/adr/001-pdf-inspector-smart-routing.md`.

## 2. Implementation Status: COMPLETE

### Phase 1: Core Inspection Upgrade
- `app/routing/inspectors.py`: Integrated `pdf_inspector.process_pdf_bytes` for sub-30ms classification, exact table detection (`pages_with_tables`), multi-column geometry (`pages_with_columns`), and CMap integrity validation, with seamless PyMuPDF fallback.

### Phase 2: Per-Page Routing Policy Calibration
- `app/routing/config.py`: Un-zeroed `metric_table_present` weight from 0.0 to 8.0.
- `app/parser/planner.py`: Added per-page band escalation (`_page_band`) so table-bearing pages route to `docling_heavy` single-page TableFormer while clean text pages remain on `rust_native`.

### Phase 3: Single-Page Execution & Memory Bounding
- Verified single-page PyMuPDF byte slicing in `docling_loader.convert_path` (`page_range=(page+1, page+1)`). Zero whole-document Docling calls.
- Verified worker pool recycling with `heavy_pool_max_tasks_per_child = 10` in `app/parser/scheduler.py` (peak host RAM < 2.5 GB RSS).

### Phase 4: Architecture Decision Record
- Authored `docs/adr/001-pdf-inspector-smart-routing.md` documenting the 3-tier routing architecture, empirical evidence from 2,017 documents, and zero-silent-loss defensive invariants.

## 3. Definition of Done: ALL PASSED
1. Production `FastInspector` leverages `pdf-inspector` with PyMuPDF fallback: **PASSED**
2. Routing policy and planner execute per-page TableFormer slicing: **PASSED**
3. Pytest suite across all routing, parser, and architecture modules: **PASSED**
4. ADR-001 authored and recorded in `docs/adr/`: **PASSED**
