# Checkpoint — run-2026-08-04-docling (ADR-007: Docling gated layout/table backend)

**Status: COMPLETE** · 2026-08-04 · Suite: **35 passed / 1 skipped** (exit 0) · Both reviewers PASS.

## Objective met
Docling is **present but triggers only where layout analysis is required**. On the Docling path the
heuristic reading order and PyMuPDF `find_tables` are replaced by Docling's learned
layout/table-structure/reading-order; the cheap native path stays the default (compute low). The
platform's trust boundary (content-addressed idempotency, versioned provenance, faithful/fallible,
events, storage) is untouched.

## What changed (code)
- `app/parser/config.py` — `layout_backend` ("native"|"docling"), `docling_models_dir`.
- `app/parser/parts.py` — `reading_order_authoritative`, `docling_version`, `layout_model`.
- `app/parser/dom/models.py` — `Provenance.docling_version`, `Provenance.layout_model` (optional).
- `app/parser/loaders/docling_loader.py` (NEW) — lazy Docling engine, on-prem cache, version-tolerant,
  DoclingDocument→RecoveredDocument mapping, tuple-shape handling, torch.compile workaround.
- `app/parser/loaders/loaders.py` — gated routing (pdf/images → Docling when configured & available).
- `app/parser/dom/builder.py` — honors authoritative reading order; records Docling provenance.
- `requirements-docling.txt` (NEW), README section.
- `tests/test_docling_loader.py` (NEW) — default/fallback/provenance/order/mapping-logic tests.

## Gates
1. Research — `RESEARCH: COMPLETE` (research.md; offline note recorded).
2. Architecture + ADR-007 — `ARCHITECTURE: APPROVED` (architecture.md).
3. Plan — `PLAN: READY` (implementation-plan.md).
4. Engineer — implemented + verified real path (engineer-report.md).
5. Architecture review — `VERDICT: PASS` (F1 models_dir singleton, F2 images-by-design).
6. Quality & perf review — `VERDICT: PASS` (similarity pair 0.50 = intentional lazy-engine pattern;
   Q1 real-path verification RESOLVED).
7. Checkpoint — this file.

## Verification findings (real Docling 2.118.0)
- torch.compile needs Triton (absent Windows/Py3.14) → `TORCHDYNAMO_DISABLE=1` + `suppress_errors`.
- docling-core `iterate_items()` yields `(item, level)` tuples → unpacked.
- Model cache forced under `models/docling/hf` (git-ignored) via env set before docling import.

## Deferred / known (append-only record)
- Per-tenant `docling_models_dir` would need the singleton to re-build per dir (currently default-path
  consistent; flagged F1).
- Bare images with `layout_backend=docling` fall back to native RapidOCR (no-OCR by design).
- GPU nondeterminism: identity anchored on source `sha256`; `docling_version`/`layout_model` in
  provenance. Cross-process DOM equality would require device/seed pinning.
- Process notes: user ran gates inline (no subagent spawns); web search/fetch blocked offline — this
  run should be treated as evidence that the org process works driven directly by the orchestrator.

## Next run (resume the track)
Module #3 — Semantic Chunking (own spec), then wire the real embedder (BGE-M3) into chunk→embed.
