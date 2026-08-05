# Gate 2 — Architecture + Trade-off Review (run-2026-08-04-docling)

`ARCHITECTURE: APPROVED`

## Decision
Add **Docling as an opt-in layout/table backend** behind the existing `RecoveredDocument` seam.
It engages only when layout analysis is required; the cheap native path remains the default.

## 1. Options considered
- **A. Replace the whole parser with Docling** — adopt `DoclingDocument` as the canonical DOM.
  Rejected: destroys our content-addressed idempotency, lineage, faithful/fallible (`None`) trust
  boundary, versioned DOM, and event contract — the platform's actual product. Also loads a heavy
  ML runtime for every doc including office/text formats. (See `docs/parser-module-spec.md` §"failures
  of naive approaches".)
- **B. Keep heuristics, add Docling as an extra model behind a flag (wrap)** — Docling present but
  gated. **Chosen.** Keeps our seam, gains Docling's layout/table fidelity where it matters, and the
  default path costs nothing extra.
- **C. Install Docling always and route all PDFs/scans through it** — rejected: compute expense is a
  first-class constraint ("keep expense low"); not every PDF needs learned layout.
- **D. Heuristic auto-detector to decide per-doc** — rejected in research (Q6): reintroduces the
  heuristics we're removing and adds a fragile classifier.

## 2. Scoring (cost, complexity, scaling, operational risk, fit)
| Dim | A | B | C | D |
|---|---|---|---|---|
| Compute cost (default corpus) | high | low | high | medium |
| Fidelity (tables, multi-col) | high | high (opt-in) | high | medium |
| Complexity / code churn | high | low-medium | low | medium |
| Offline/on-prem risk | high | low | medium | medium |
| Fit to our trust boundary | poor | good | poor | poor |

## 3. Chosen option + reasons
Option **B**. Reasons:
- Our DOM/harness is the product (trust/verification); Docling is a *backend*. A loader swap is the
  seam we already built (`parts.py`: "the seam that keeps format parsers interchangeable").
- "Docling will be present but will trigger where layout analysis is required" maps exactly to a
  config-driven backend switch + auto-engage on scanned images (no native text ⇒ layout analysis
  required).
- "Remove our heuristics-based Layout-Engine" → for the Docling path, `reading_order.py`'s naive
  top-bottom-left-right is bypassed; Docling's reading order drives the DOM chain. The heuristic
  stays as the fallback for formats Docling doesn't cover (and as the cheap PDF path default).
- "Traditional Tables extraction — Remove. Replace with Docling" → `find_tables()` is bypassed on
  the Docling path; `TableItem`'s cell grid maps to `RecoveredTable`.

## 4. Challenge (attack my own choice)
- *Risk:* Docling is heavy and version-unstable; wrapping it lazily could rot behind API drift.
  Mitigation: lazy import + feature-sniff + pinned version in provenance, mirroring `ocr.py`; if
  Docling is absent, the loader degrades to the native path (never crashes the pipeline).
- *Risk:* someone flips `layout_backend=docling` on the whole corpus and blows the GPU/CPU budget.
  Mitigation: default stays `native`; docling only engaged by explicit config or when a scanned
  doc needs it; model loaded once per process.
- *Risk:* "remove heuristics" not fully honored (native path still uses them). Mitigation: this is
  intentional and recorded — heuristics remain as the *default cheap path*; the *Docling path*
  fully replaces them. The two are exclusive per document.
- *What would change my mind:* evidence that Docling's per-doc CPU cost is acceptable at corpus
  scale AND that its reading-order quality justifies making it the default for all PDFs. Until
  measured, it stays opt-in.

## 5. Architectural shape (modular monolith, no boundary changes)
```
app/parser/
  config.py                    + layout_backend: "native"|"docling"  (versioned, in provenance)
  loaders/loaders.py           _pdf/_image route: layout_backend=="docling" → docling loader
  loaders/docling_loader.py    NEW: lazy Docling singleton; DoclingDocument → RecoveredDocument
                               (blocks/kind/bbox/page/text, tables header+rows, images, reading order)
  dom/reading_order.py         unchanged; used only by the native path
  dom/models.py                Provenance + docling_version, layout_model (optional)
  storage.py                   unchanged
  events.py                    unchanged
  requirements.txt             docling under an optional extra (pip install .[docling])
```
- `docling_loader.py` mirrors `ocr.py`'s lazy engine pattern: `_engine is None` → try import, else
  `False`; `engine_available()` / `engine_name()`; model cache under `models/docling/`.
- Reading order: the Docling path materializes our `reading_order` chain from `iterate_items()`
  order instead of the heuristic. Native path unchanged.

## 6. Out of scope this run
- Changing the DOM schema or event contract. Document stays `Document`; `provenance` gains two
  optional fields only.
- Making Docling the default for all PDFs (needs a benchmark we don't have).
- DICOM / new formats.

## ADR appended to `project_memory/architecture_decisions.md`.
