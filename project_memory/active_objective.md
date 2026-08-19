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
`run-2026-08-19-page-centric`

## Objective
Redesign the parser execution model so that the **page becomes the fundamental processing and durable-storage unit**, while the **document remains the orchestration unit**. Eliminate silent page loss from `std::bad_alloc` and similar OOM crashes by making the pipeline resource-aware, hardware-scaling, and resilient.

**Problem statement:** Docling's C++ layout engine allocates large per-instance heaps. Running multiple document-level Docling workers concurrently exhausts RAM, causing `std::bad_alloc` in the C++ preprocess stage. Docling swallows these per-page exceptions and returns partial documents. The pipeline reports these as "success" — creating silent data loss (e.g. 24-page PDF truncated to 10 pages). This is not a 4GB GPU problem; it will occur on any system when concurrency exceeds what the heavy ML engines can tolerate.

**User directive:** "I want a design which will scale depending on the hardware instead of limiting."

## Scope
- **Execution model redesign:** Transform from single-pass document-level parsing (`Detect → Route → Load(whole doc) → Build → Store`) to a page-centric pipeline.
- **Resource-aware scheduling:** Decouple thread pools so cheap native tasks and heavy ML tasks (Docling, OCR) run under separate, hardware-scaling concurrency limits. The heavy pool must be explicitly capped based on available system RAM / GPU memory, not total worker count.
- **Page as durable unit:** Write per-page intermediate DOM outputs to storage. Track execution progress in a document ledger for resume/retry without reparsing completed pages.
- **Zero silent page loss:** Every page must have an explicit status. Document assembly must validate `assembled_page_count == expected_page_set` before marking success.
- **Docling page bounding:** Use `page_range=(p,p)` to bound peak C++ memory per job to a single page rather than scaling linearly with document length.
- **Intelligent router integration:** The existing router (ADR-011) decides the route band per document; page-level execution applies the route uniformly across pages, with enrichment exceptions for individual scanned pages.
- **Backward compatibility:** Existing CLI (`app.processing.cli`), existing `Extractor.extract()` API, and existing DOM schema must remain functional. The redesign is an internal execution-model change.

## Constraints
- Follow `docs/org-gate-protocol.md` hard gates.
- Do not build a distributed system. Keep the modular monolith.
- Do not add external databases. Filesystem storage only.
- Do not invent hardware detection heuristics without evidence (research gate required).
- Preserve the canonical DOM contract; do not modify `Document`, `Page`, `Block` schemas unless additive.
- Tests are part of done.

## Definition of done
1. Both reviewers emit `VERDICT: PASS`.
2. `.venv/Scripts/python.exe -m pytest tests/ -q` green.
3. Run the real test corpus (`C:/Users/Asus/Downloads/test_cases`: 12 PDFs + 3 images) and verify:
   - Zero `std::bad_alloc` or OOM crashes.
   - Zero silent page loss (every document reports actual vs expected page count).
   - All 15 documents parse successfully with correct routing.
4. Knowledge Curator checkpoint at `checkpoints/run/run-2026-08-19-page-centric/checkpoint.md`.
5. Final report at `checkpoints/run/run-2026-08-19-page-centric/final-report.md`.
6. ADR(s): new ADR for page-centric execution model + resource-aware scheduling.

## Notes for the team
- Authority is the user's message + existing ADRs + `CLAUDE.md`.
- Prior runs: routing engine (run-2026-08-06-router) built the decision-only router. This run builds the execution engine that *uses* those decisions safely.
- The test corpus is the ground truth for verification. The redesign must pass it.
- Do not over-engineer. The goal is bounded peak memory + zero silent drops, not a full distributed orchestrator.