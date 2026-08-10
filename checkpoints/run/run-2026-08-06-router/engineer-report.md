---
run: run-2026-08-06-router
wave: engineer
status: complete
---

# Engineer Report — Intelligent Document Router (run-2026-08-06-router)

Implemented the ratified routing spec (`docs/routing-spec.md`), the approved
architecture, and `implementation-plan.md` (PLAN: READY, waves A–I) plus the
two orchestrator gap rulings. Full suite green: **144 passed, 1 skipped** (was
111 passed before this run). Baseline assumption: `layout_backend` default now
`"auto"` per ADR-007 amendment.

## What was built (per wave)

- **A — leaf schema + config** — `app/routing/schema.py` (Signal /
  RoutingDecision; leaf, imports only pydantic), `app/routing/config.py`
  (`RoutingConfig` frozen snapshot: bands, versioned weights, low-conf
  thresholds, snapshot()). `app/routing/__init__.py` package.
- **B — inspector** — `app/routing/inspectors.py`: `InspectorFeatures` +
  `FastInspector` (PyMuPDF `fitz.open(stream=..., filetype="pdf")` open-without-
  render; no render/OCR/Docling). Missing observations are `None`, never 0.
  Per-page char count, image refs, **continuous `pages_image_ratio` (auditable
  evidence)**, full-image-page audit list, multi-column heuristic, table
  presence (finder result else `None`).
- **C — detectors** — `app/routing/detectors/`: `base.py` (`Detector` ABC with
  `can_evaluate`/`evaluate` failure-isolation + `register_detector` registry)
  + the 9 detectors (metadata/text/image/layout/ocr/table/form/reading_order/
  font), each `ok/failed/not_applicable` with failure never a negative.
- **D — scoring + policy** — `app/routing/scoring.py` (`Scorer` Protocol +
  deterministic `WeightedHeuristicScorer`, no band knowledge),
  `app/routing/policy.py` (`RoutingPolicy`: 3 config bands, conservative
  escalate-one-tier-on-low-confidence, never downgrade).
- **E — router** — `app/routing/router.py`: assembly
  inspector→detectors→scorer→policy→RoutingDecision, versioned fields +
  `inspection_time_ms` + `RoutingStats` (per-band/failures/score+conf
  histograms/missing/unknown signal counts, ring of recent decisions).
- **F–G — surgical integration** — `app/parser/config.py`
  (`layout_backend="auto"` default, `routing` knob, snapshot); `dom/models.py`
  `Provenance.routing` (additive, old-DOM-safe `None`); `parts.py`
  `RecoveredDocument.routing`; `dom/__init__` re-export; `extraction.py`
  computes route after detection + forwards decision; `loaders/loaders.py`
  `load(..., route=)` dispatch (native/enrich/docling); `dom/builder.py` maps
  routing into Provenance; `route` added to `document.parsed.v1` payload/tier
  report.
- **H — enrichment** — `app/parser/loaders/enrichment.py`
  `enrich_scanned_pages(rec, config, *, data, pages=None, ocr_fn, max_pages)`:
  native + OCR of no-text-block pages (one render/page, reuses ocr.ocr_bytes,
  per-page try/except, `max_pages` CPU cap).
- **I — tests** — `tests/routing_fixtures.py` (shared PDF builders) +
  `tests/test_routing_{schema,config,inspector,detectors,scoring_policy,router,
  provenance,enrichment}.py` and `tests/__init__.py` (package marker).

## The two gaps (orchestrator rulings)

- **Gap A (images)**: a standalone image under `"auto"` keeps the existing
  native OCR loader — the router returns `None` for non-PDF, so it is never
  sent to Docling. Verified: `extract(<png>)` → `detected.slug=="png"`,
  `provenance.docling_version is None`, `report.route is None`.
- **Gap B (unknown signal)**: a `Signal.name` not in `RoutingConfig.weights` is
  warned, skipped, and counted into `RoutingStats.unknown_signal_count` — never
  a crash, never a negative. Enforced by both a detector-coverage test and a
  router regression (`_WeirdDetector` emitting `not_a_real_signal`).

## Auditable-routing corrections (per coordinator review)

1. **Continuous scanned evidence replaces the hard boolean.** `InspectorFeatures`
   now records `pages_image_ratio: dict[int,float]` (image-rect area / page
   area, exact 4 d.p., computed without a render) plus existing per-page char
   counts/image refs/dims — the auditable evidence the heuristic is judged on.
2. **Scanned probability is a detector-side function of that evidence, guarded
   by text.** `metric_scanned_page_probability = max over pages of
   (image_ratio if that page has 0 printable chars else 0)`. A decorative
   border/logo raster WITH real embedded text is therefore NOT scanned.
3. **Multi-column faithfulness.** Confirmed `est_multi_column_pages` fires for a
   true 2-column PDF; raised the multi-column + reading-order weights in
   config so a genuinely multi-column doc lands in **Enrichment** (31+) instead
   of Native, and added a certificate-fixed regression.

## Router + enrichment smoke (end-to-end, real RapidOCR engine installed)

| doc | evidence (img_ratio / chars) | route | cpx | conf |
|-----|------------------------------|-------|-----|------|
| plain text | 0.0 / 96 | native | 1 | 0.84 |
| scanned (blank raster) | 0.9428 / 0 | enrichment (OCR) | 52 | 0.86 |
| two-column | 0.0 / 79 | enrichment | 32 | 0.84 |
| certificate (bordered + text) | 0.9103 / 130 | native | 5 | 0.84 |

The certificate case — the coordinator's spurious-Docling worry — now routes to
**native** (text fully recovered, cheapest sufficient tier; NOT Docling). The
scanned case routes to **enrichment** (OCR the blank page), not Docling, which
is cheaper and correct per spec §7. Enrichment adds `source="ocr"` blocks with
`ocr_engine` provenance (proven unit-level via injected `ocr_fn`; the synthetic
blank-grey raster legitimately yields no OCR text glyphs).

## New test count
28 new tests added across the 8 routing test files (+ the `__init__`/fixtures
pkg marker). Net suite: **144 passed, 1 skipped** (was 111 + 1 skip before the
run). The existing `test_default_layout_backend_is_native` was updated to
`test_default_layout_backend_is_auto` (ADR-007 amendment), and
`test_image_doc_reparse_deterministic` normalizes the wall-clock
`inspection_time_ms` before byte-equality (a measurement, not part of route
determinism — documented in the test).

## Deviations + rationale
- **`enrich_scanned_pages` needs `data` (the source PDF bytes) to render the
  empty pages.** The plan's listed signature omitted them, but rendering is the
  point of the OCR pass; `data` is a required named kwarg (the reserved
  page/region-selectivity seam is preserved). With `data=None` it degrades to a
  safe no-op.
- **Docling tier is reached only for whole-document multi-concern complexity**
  (scanned + multi-column + table), not a lone scanned page — a lone scanned
  page now goes to Enrichment(OCR), which fixes it at the cheapest tier. This
  is a defensible v1 conservative posture; the top-tier calibration belongs on
  the real `_cli_out` corpus (ADR-011 challenge), where the weights are a config
  change, not code.
- Multi-column / reading-order weights were raised from the plan's initial
  guesses so a 2-column doc reaches Enrichment (faithfulness fix); these remain
  config-calibration values to re-tune against the corpus.

## Known limitations / flagged for the architect
- `inspection_time_ms` is a wall-clock measurement persisted in provenance; it
  is the only non-deterministic routing field (excluded from the determinism
  equality tests by design).
- Invalid/unreadable PDFs route as `native` at low confidence with a
  "cannot inspect" reason (never fabricated evidence).
- Standalone images are intentionally NOT routed in v1 (Gap A optional stamp
  omitted) — they keep the native OCR loader.