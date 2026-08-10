# Final Report — run-2026-08-06-router

**Objective:** Implement the Intelligent Document Router per `docs/routing-spec.md` — a separate, deterministic, explainable, configurable, extensible decision layer between ingestion and extraction.

**Result:** ✅ Both reviewers **PASS** · pytest **159 passed / 1 skipped** · calibrated on the real `test_cases` corpus.

## What was built
- **`app/routing/`** (ADR-011): `FastInspector` (PyMuPDF open-without-render, decision-free), **9 pluggable detectors** (Metadata/Text/Image/Layout/OCR/Table/Form/ReadingOrder/Font) via a registry (`register_detector`), **absolute-sum complexity scorer** (`Scorer` Protocol; config weights ARE the 0-100 band map), **3-band policy** (0-30 native / 31-60 enrichment / 61-100 docling) with **conservative escalate-on-low-confidence** (never downgrade), failure-isolated detectors, unknown-signal warn+skip+count, deterministic+versioned `RoutingDecision` persisted into `Provenance.routing`.
- **Integration (surgical, backward-compatible):** `layout_backend` default → `"auto"` (router-driven), `"native"`/`"docling"` kept as overrides; extraction routes after detection; `Loaders.load(route=)` dispatch; route on the `document.parsed.v1` event. **ADR-012**: Enrichment band = native + OCR of no-text-block pages (`app/parser/loaders/enrichment.py`, reuses the fixed `ocr.ocr_bytes`) — closes the deferred "PDF OCR fallback."

## Calibration (on the real `test_cases` corpus)
The scoring was corrected (absolute-sum instead of a total-weight dilution — the original under-scored every complex/scanned doc) and weights tuned so the scan cluster caps below the Docling band:

| Class | Route | Examples |
|---|---|---|
| Scanned tickets / receipts / Report / cert | **enrichment** (OCR) | Nizammudin, Agra, Tundla, receipt1/2, Report (these were coming out **empty** before) |
| Complex academic text | **docling** (layout/reading-order) | 2503/2504/3548 papers, PDF v3 |
| Simple text | **native** | (synthetic test) |

**Known limitation (tracked, ADR-011):** the cheap detectors under-report MDPI-style academic layout (electronics → enrichment, not docling); detector refinement is the top follow-up. Basic scans are reliably routed.

## Review + fix round
- Architecture reviewer **PASS**; Quality reviewer **PASS**.
- Fix round addressed: **M1** corpus test non-hermetic → skip-guard → portable; **M2** `find_tables()` dominated inspection latency (~4s) → capped to a page budget (keeps the inspector « processing, spec §14); **M3** "no table found" conflated with "table detection failed" → returns a measured negative; **m7** dead code removed + typo.
- Accepted/reviewer minors recorded as follow-ups (reuse one geometry pass; record the *executed* tier when Docling falls back to native; deepen per-detector tests).

## Decision files
- **ADR-007 amended** (Docling default → auto-router) · **ADR-011** (Document Router) · **ADR-012** (OCR-in-PDF) — in `project_memory/architecture_decisions.md`; **`questions.md`** resolves the deferred PDF-OCR item + tracks follow-ups; **`module_status.md`** + **`MEMORY.md`** updated.

## Process note
The engineer subagent hit repeated server errors mid-build; the Orchestrator completed the module, then applied the calibration + review-fix round directly (documented). The reviewers still added real value (found the table negative-vs-missing conflation and the inspection-latency hotspot).

## Where the project stands
Parser (Docling-gated) → Normalizer → batch → **Document Router** → embeddings → **semantic chunks**: the ingestion+intelligence stack is in place. **Next:** Module #4 retrieval, then ontology/KG, then the validation framework. The router's future work is detector refinement and calibration on a larger corpus.