---
name: active-objective
description: The run brief — the objective and notes the autonomous organization executes next. Overwrite the body for each new run; never delete the file.
metadata:
  type: project
---

# Active Run Brief

> The Project Orchestrator (`/dev-team` in-session, or the `audit` workflow in background)
> reads this file at the start of a run. Replace the contents for a new run; keep this file.

## Run id
`run-2026-08-06-router`

## Objective
Implement the **Intelligent Document Routing Engine** per **`docs/routing-spec.md`** (the ratified
authority — read it fully, it is the contract). Replace the static `layout_backend` heuristic with a
separate, deterministic, explainable, configurable, extensible decision layer between ingestion and
extraction, so each document is routed to the cheapest pipeline (Native / Enriched / Docling) that
reliably delivers the required fidelity.

## Scope
- **New `app/routing/` module**: `config.py` (decision/policy versions, 3 score bands, per-detector
  weights — all config), `inspectors.py` (FastInspector: cheap pre-parse feature pass, decision-free),
  `detectors/` (pluggable, one detector per concern — Metadata/Text/Image/Layout/OCR/Table/Form/
  ReadingOrder/Font), `router.py` (aggregates detector signals → complexity_score 0–100 + confidence
  + route + structured reasons; determinism + versioning), `schema.py` (RoutingDecision pydantic).
- **Separation:** Inspector answers "what can I cheaply observe?"; Router answers "which pipeline?";
  detectors answer "what do I note?"; pipeline executes the decision. No routing logic in extraction;
  no extraction logic in the router; no pipeline-execution in detectors.
- **Routing policy (config):** 0–30 → Native · 31–60 → Enrichment · 61–100 → Docling.
  Conservative toward complex docs on low confidence (defined fallback).
- **Metadata:** persist `routing` block (route, complexity_score, confidence, reasons, router + policy
  + detector versions, inspection_time_ms, signals) into `Document.provenance.routing` (optional,
  additive — old DOMs must keep validating) and reason-generating from detector results.
- **Integration (surgical):** `ParserConfig.layout_backend` becomes `"auto"` (default, router-driven)
  while keeping `"native"`/`"docling"` as manual overrides. Extraction runs the router after type
  detection and dispatches to native / enrichment / docling loaders. Existing loaders unchanged;
  only the dispatch switch is routing-aware.
- **Enrichment band (v1, simple):** native extraction + OCR of pages that yield no text blocks
  (scanned-page fallback via the existing/writable `ocr.ocr_bytes`). Design interfaces so future
  page/region selectivity isn't precluded, but do not build page-level orchestration now. Page-based
  re-embedding is NOT part of v1.

## Constraints
- Follow `docs/routing-spec.md` §15–§16: preserve the canonical-DOM contract; do not leak routing
  into the rest of the parser; do not modify unrelated modules; keep v1 simple (strong architecture,
  no ML model / distributed infra / plugin-discovery framework / external DB / page-level orchestration).
- Trust boundary preserved: deterministic, idempotent, faithful, provenance-recorded, on-prem.
- Existing behavior must not change for docs whose routing decision is Native; regression tests
  persist representative docs + expected decisions.

## Definition of done
1. Both reviewers emit `VERDICT: PASS`.
2. `.venv/Scripts/python.exe -m pytest tests/ -q` green, including new router tests (detector,
   scoring, routing bands, boundary 30/31/60/61, missing-signal, detector-failure, determinism,
   regression).
3. Knowledge Curator checkpoint at `checkpoints/run/run-2026-08-06-router/checkpoint.md`.
4. Final report at `checkpoints/run/run-2026-08-06-router/final-report.md`.
5. ADR(s): amendment to ADR-007 (Docling default → auto-router) + new ADR-011 (Document Router) +
   ADR-012 (OCR in PDF for scanned pages); questions.md (close the deferred PDF-OCR item;
   record the Hindi/multilingual-OCR decision as tracked); module_status.md updated.

## Notes for the team
- Authority is `docs/routing-spec.md`. The routing module must be its own decision layer; do not
  spread detector logic across the parser. Detectors register via a pluggable hook; adding one must
  not touch the router algorithm or pipelines.
- The real test corpus (this run's verification corpus = `_cli_out` from `C:/Users/Asus/Downloads/
  test_cases`) has 12 PDFs + 2 JPGs incl. text papers, a scanned ticket, receipts, and an image-based
  certificate — use it to sanity-check routing decisions (native vs enrichment vs docling) and that
  reading order improves where the router sends docs to Docling.
- Prior runs: audit (deadlock/storage fixes), docling (ADR-007 gated backend), chunking (Module #3).
  This run builds the router; it does NOT rewrite the native heading heuristic or chunking unless
  required to integrate cleanly (those are tracked follow-ups).