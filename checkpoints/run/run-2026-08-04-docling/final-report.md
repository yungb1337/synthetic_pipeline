# Final Report — run-2026-08-04-docling

**Objective:** Integrate Docling as a *gated* layout + table-structure engine — present, but
triggering only where layout analysis is required (low compute); on that path it replaces the
heuristic reading-order and PyMuPDF `find_tables` extraction.

**Outcome: DONE.** Suite **35 passed / 1 skipped**, both reviewers **PASS**.

## What each gate produced
| Gate | Result |
|---|---|
| 1 Research | `RESEARCH: COMPLETE` — Docling wins on learned layout/tables/ROG; our seam wins on determinism/lineage/offline; recommendation: Docling as a loader, not a replacement |
| 2 Architecture | `ARCHITECTURE: APPROVED` + **ADR-007** — gated backend behind `RecoveredDocument`; native stays default |
| 3 Plan | `PLAN: READY` — 8-file change set |
| 4 Engineer | Implemented; real `docling 2.118.0` verified; 2 env issues fixed in code |
| 5 Arch review | `VERDICT: PASS` (2 minor notes) |
| 6 Quality review | `VERDICT: PASS` (1 intentional-pattern note; Q1 resolved) |
| 7 Checkpoint | `checkpoint.md` + blackboard updated |

## What changed
- **`app/parser/loaders/docling_loader.py` (new)** — lazy Docling engine mapping
  `DoclingDocument → RecoveredDocument`; on-prem model cache (`models/docling/`, ~506M, git-ignored);
  version-tolerant API; bakes in `TORCHDYNAMO_DISABLE` (no Triton on Windows/Py3.14) and
  `iterate_items()` tuple unpacking.
- **Config/seam/DOM** — `layout_backend` + `docling_models_dir` knobs; `reading_order_authoritative`;
  `Provenance.docling_version`/`layout_model`.
- **Routing** — PDFs/images use Docling only when `layout_backend=="docling"` and available; any
  failure falls back to native (never crashes).
- **Builder** — Docling reading order is authoritative on that path (heuristic ROG removed from it).
- **Tests** — new `tests/test_docling_loader.py` (default, fallback, provenance/order, mapping logic).
- **Dep** — optional `requirements-docling.txt`; README section.

## Gate/verification status
- Architecture: `PASS` · Quality & Performance: `PASS` · pytest: green (exit 0, 1 intentional skip).

## Where the run left off
The parser's layout/table path is now Docling-backed behind the gating flag. **Next run brief:**
resume Module #3 — Semantic Chunking — then wire the real BGE-M3 embedder into chunk→embed.
