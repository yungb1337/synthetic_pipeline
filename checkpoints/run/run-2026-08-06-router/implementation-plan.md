---
run: run-2026-08-06-router
authority: docs/routing-spec.md (ratified 2026-08-06)
design-source: checkpoints/run/run-2026-08-06-router/architecture.md (ARCHITECTURE: APPROVED)
brief: project_memory/active_objective.md
stage: plan
---

# PLAN: READY

Implementation plan for the **Intelligent Document Router**. Decomposes the approved architecture
(architecture.md) into implementation-ready, dependency-ordered tasks. No design decisions are made
here beyond what the architecture specifies. Two real gaps are **flagged** (§ Gap) for the architect,
not resolved in this document.

---

## Scope guard (from architecture + brief, do NOT cross)

- **Routing only applies to PDFs in v1.** The `FastInspector` opens `fitz.open(stream=…, filetype="pdf")`.
  The `route` is computed only when `detected.slug == "pdf"` **and** `config.layout_backend == "auto"`.
  Non-PDF formats keep their existing loader path entirely (native). Manual `"native"` / `"docling"`
  overrides are preserved. *(This is the architecture dir + spec §15/§16; the corpus-images path is
  the existing native image/OCR loader — see Gap A.)*
- **No** ML model, distributed infra, plugin-discovery framework, external DB, page-level orchestration,
  page/region selectivity (v1). Interfaces must not *preclude* them (named-arg seams only).
- Do **not** modify unrelated modules. Only the listed files change. Existing DOMs stay valid.
- Detectors/scoring carry no pipeline-execution logic; router carries no extraction logic; parser only
  dispatches on the decision.
- Same bytes + same `RoutingConfig` snapshot + same detector/scorer versions ⇒ identical decision.
- Calibration values (weights, thresholds) are **initial guesses**; they live in `RoutingConfig`
  (config), never code.

---

# WAVE A — `app/routing/` scaffolding: leaf schema + config

### A1. `app/routing/schema.py` — leaf evidence models
- **File(s):** `app/routing/schema.py` (new), `app/routing/__init__.py` (new, package docstring).
- **Behavior:** Imports **only** `pydantic` (LEAF — parser/dom may import it, no cycle). Define:
  - `Signal(BaseModel)`: `detector`, `version`, `name`, `value: Optional[float|bool|str] = None`,
    `confidence: Optional[float] = None` (0..1, None = not established),
    `evidence: Optional[str] = None`, `status: str = "ok"`
    (`"ok"|"failed"|"missing"|"not_applicable"`). A missing observation ⇒ `value=None` + `status="missing"`
    — never coerced to 0/False (spec §4 §11). No defaults fabricate evidence.
  - `SignalValue` is bounded: `value` accepts int/float/bool/str/None only.
  - `RoutingDecision(BaseModel)` exactly per architecture §6:
    `route: str`, `complexity_score: int`, `confidence: float`, `reasons: list[str]`,
    `signals: list[Signal]`, `router_version`, `policy_version`, `scoring_version`,
    `detector_versions: dict[str,str]`, `inspection_time_ms: float`,
    `bands: dict[str, tuple[int,int]]` (band → (lo,hi) for audit/regression).
  - `bands` is a plain `dict` for audit; no pydantic coercion of tuple.
- **Determinism contract:** the model holds only present fields; version fields are filled by config,
  not computed.
- **Test(s):** `tests/test_routing_schema.py`
  - signal `value=None` + `status="missing"` round-trips through `model_dump_json`.
  - a `failed`/`missing` signal is never rendered as a positive evidence string.
  - `RoutingDecision` validates a full example and `signals=[]` from a failed detector.
- **Definition of done:** `schema.py` imports only pydantic; `from app.routing.schema import RoutingDecision, Signal` works with no parser import; schema tests green.

### A2. `app/routing/config.py` — `RoutingConfig` immutable snapshot
- **File(s):** `app/routing/config.py` (new).
- **Behavior:** `@dataclass(frozen=True) RoutingConfig`, versioned + weight table (config, not hardcoded §6):
  - `router_version: str = "router-v0.1.0"`, `scoring_version: str = "scoring-v0.1.0"`,
    `policy_version: str = "policy-v0.1.0"`.
  - `bands: tuple[tuple[int,int,str], ...] = ((0,30,"native"),(31,60,"enrichment"),(61,100,"docling"))`.
  - `weights: dict[str,float] = _DEFAULT_WEIGHTS` — signal_name → weight; the **only** place weights live.
    Provide `_DEFAULT_WEIGHTS` (initial guesses, to calibrate against `_cli_out`, footnote it).
    Coverage at least: scanned-page prob, page-char-density, text-ratio, multi-column prob,
    image-density/full-image-pages, OCR-needed, table-prob, form-prob, reading-order-ambiguity,
    font-diversity, low-text-density, extraction-confidence.
  - `native_low_conf: float = 0.50`, `enrichment_low_conf: float = 0.35` (conservative escalation thresholds).
  - `inspection_engine: str = "pymupdf"`, `max_signals: int` cap for safety.
  - `snapshot() -> dict` (JSON-safe fingerprint, matches `ParserConfig.snapshot()` style) for determinism
    provenance.
  - Optional: `max_bytes` for inspection skip on oversized docs.
- **Test(s): `tests/test_routing_config.py`**: default bands parse to 3 ordered bands covering 0..100
  contiguously; every default weight is ≥0; `snapshot()` is stable (two dumps identical); thresholds in [0,1].
- **DoD:** weights & bands live only here; `RoutingConfig` imports no app module (only `dataclasses`).

---

### WAVE B — Inspector (decision-free feature pass)

### B1. `app/routing/inspectors.py` — `InspectorFeatures` + `FastInspector`
- **File(s):** `app/routing/inspectors.py` (new).
- **Behavior:**
  - `@dataclass InspectorFeatures` per architecture §4: metadata (mime_slug, declared_extension,
    pdf_version, encrypted, producer, creator, outline, tag, page_count, page_dims), text
    (pages_char_count, chars_per_page, text_ratio, fragment_count), image (image_count,
    images_per_page, covered_pages, full_image_pages), layout hints (est_multi_column_pages,
    block_count_per_page), structural (`detected_tables: int|None`), and total entity counts.
    All fields defaulted; **a feature that could not be observed is None, not 0**.
  - `FastInspector.open(data) -> InspectorFeatures | None`: `fitz.open(stream=data, filetype="pdf")`
    ONLY — no `Pixmap`, no render, no image decode. Read `doc.metadata`, `doc.get_toc()` (outline),
    `encrypted`, page count, page sizes, and per page `get_text("rawdict")` (spans) + `get_images(full=True)`.
    Returns `None` on non-PDF/unreadable (caller treats as no-route).
  - **Decision-free guarantee:** the inspector never reads `config.layout_backend`, policy, or score —
    it observes only.
  - `est_multi_column_pages`, `full_image_pages`, `text_to_area_ratio` are cheap heuristics (block
    x-overlap clustering / image-area per page) — marked "heuristic" in the dataclass comment.
  - Wrap per-page extraction in try/except so one bad page degrades to `missing`, never throws.
- **Test(s): `tests/test_routing_inspector.py`:**
  - a text-only PyMuPDF-generated PDF yields `text_ratio > 0`, `full_image_pages == []`, `page_count == N`.
  - a page holding only a full-bleed image yields that index in `full_image_pages` and zero chars
    on that page (text-embedded absent ⇒ `missing`, not 0-confirm).
  - `FastInspector(data) ` on a non-PDF → `None`.
  - determinism: same bytes → equal features (no hidden RNG).
- **Definition of done:** inspector never triggers render/OCR/Docling; features expose `missing`;
  per-page failures isolated.

---

### WAVE C — Detectors (per concern; each independently testable)

### C0. `app/routing/detectors/base.py` + registry + plugin hook
- **File(s):** `app/routing/detectors/base.py` (new), `app/routing/detectors/__init__.py` (new).
- **Behavior:**
  - `class Detector(ABC)` per architecture §3: `name: str`, `version: str`, `can_evaluate(feats) -> bool`,
    `evaluate(feats) -> list[DetectorResult]` (wraps in try/except; on any exception returns
    `DetectorResult(status="failed", error=str(e), signals=[])` — re-raise **never** §11).
  - `@dataclass DetectorResult`: `detector`, `version`, `status` (`"ok"|"failed"|"not_applicable"`),
    `error: str|None`, `signals: list[Signal]`.
  - `DetectorSignals` aggregation helper: collects `Signal`s across results, preserving order + status.
  - **Registry + plugin hook (architecture D1: plain list, additive, no plugin framework):**
    - `detectors/__init__.py` defines `DETECTOR_PRIORITY: list[str]` and `get_detectors() -> list[Detector]`
      returning the 9 instances.
    - a public `register_detector(detector_cls)` hook appends to the registry so tests add a stub
      detector without touching router/pipeline (additive §5/§16). Registry remains a list, no
      auto-discovery.
- **Test(s):** `tests/test_routing_detectors_core.py`: `can_evaluate=False` ⇒ result status
  `not_applicable`, no positive-leaning signals; a stub detector that raises is caught and produces
  `failed` (no propagate); registry reflects `register_detector`.

#### C1. MetadataDerector — `meta_detector.py`
- name "metadata", version "1.0.0".
- Signals: `metric_foundation_meta_available` (bool, missing at explicit), pdf_version, encrypted,
  producer/creator present, `has_outline`, `has_tag`. Outputs evidence like `"producer=Acrobat"`.
- Behavior: but a PDF with no producer → `value=None`/`missing`, never treated as low-quality.
- DoD: validations that `encrypted` etc. `missing` when PyMuPDF returns None / no value.

#### C2. TextDetector — `text_detector.py`
- Signals: `metric_text_density`, `metric_text_ratio`, `metric_char_per_page`,
  `metric_low_text_density` (bool when chars/page below a config floor), `metric_fragment_count`,
  `metric_text_extraction_confidence` (from text/Fonts). Empty-text page → page with no text blocks
  accumulated but NOT a false negative (a "textless/scanned" page is a `missing`-recorded evidence that
  drives an OCR/scan heuristic only when confident).
- DoD: a page with zero text yields `status="missing"` value (e.g. `metric_page_char_density_none`),
  never a `0`/`False` "positive".

#### C3. ImageDetector — `image_detector.py`
- Signals: `metric_image_count`, `metric_images_per_page`, `metric_full_image_pages`
  (from `full_image_pages`), `metric_scanned_page_probability` (full-image w/o text → scan probability).
- Can-evaluate False for non-image-capable (e.g. no image slots) → `not_applicable`, not negative.
- DoD: image-heavy/page with full-bleed raster → high scan prob signal; no image → `missing`.

#### C4. LayoutDetector — `layout_detector.py`
- Signals: `metric_multi_column_pages` (est_multi_column_pages), `metric_layout_complexity`
  (normalized block_count spread + column count), `metric_block_overlap` (spatial fragmentation).
- DOI: multi-column layout → moderate image but the weight only raised if both image AND text present.

#### C5. OcrDetector — `ocr_detector.py`
- Determines whether OCR is needed/possible. Signals: `metric_ocr_required`, `metric_ocr_confidence`
  (from low-text-density + image-scan prob; never invoke OCR during inspection).
- `can_evaluate` false for text-borne docs (high text density) → `not_applicable`, and does NOT imply
  a reading-order penalty.

#### C6. TableDetector — `table_detector.py`
- Signals: `metric_table_probability` (from `detected_tables: int|None` + layout).
- **Key §11 rule:** if table detection **failed** (e.g. find_tables raised / unavailable), record
  `DetectorResult(status="failed")`, `Signal(status="failed")` — MUST NOT emit "no table" (`missing` is
  not `0`). DoD: the failure is recorded, not a valid negative.

#### C7. FormDetector — `form_detector.py`
- Signals: `metric_form_probability` (heuristic from line/bbox overlapping regions / key-value pairs).
- `can_evaluate` false where form impossible → `not_applicable`. Missing never treated as no-form.

#### C8. ReadingOrderDetector — `reading_order_detector.py`
- Signals: `metric_reading_order_ambiguity` (block-overlap/columns/z-order heuristic), confidence.
- Decision-free: never reorders; only reports ambiguity (spec §3).

#### C9. FontDetector — `font_detector.py`
- Signals: `metric_font_diversity`, `metric_font_embedding`, `metric_unusual_font`.
  Reads font info from text spans (embedded font list). Missing → `missing`, never defaulted.

- **Cross-cutting:** every detector `evaluate` is wrapped (C0) so a failing detector never crashes a
  document and never manufactures a negative. Each detector lists its `[signal_name]` mapping and
  **any signal it emits must also be present in `RoutingConfig.weights`** (regression guard, Gap B).

---

### WAVE D — Scoring abstraction + policy

### D1. `app/routing/scoring.py` — `Scorer` Protocol + `WeightedHeuristicScorer`
- **File(s):** `app/routing/scoring.py` (new).
- **Behavior:**
  - `class Scorer(Protocol)`:`score(self, signals: list[Signal], features: InspectorFeatures) -> Score`
    where `Score` = `(complexity: float 0..100, confidence: float 0..1, reasons: list[str])` (dataclass).
  - `WeightedHeuristicScorer(routing_config)` — **deterministic**: for each `Signal` with a weight in
    `config.weights`, add `clamp(signal.value * weight, 0, weight)` etc. Weighted sum → clamp 0..100.
    Normalize to [0,100].
  - **Confidence** = (a) share of band-critical signals that are `missing`/`failed` (more missing ⇒
    lower confidence) × (b) agreement among strongest signals. Low-confidence caused by `missing`, not
    by a negative.
  - **reasons** collected from strongest positive signals + the band-critical missing/failed signals
    (§8), human readable (e.g. `"high scanned-page probability (0.9)"`).
  - `Scorer` has **no knowledge of bands** — bands applied by policy outside scorer (architecture §5).
- **Test(s): `tests/test_routing_scoring.py`:**
  - pure text PDF → low complexity, high confidence.
  - image-scan-heavy / all-missing-text → high complexity (scan penalty), lower confidence.
  - no signals / all missing → low confidence (never a false-confidence high).
  - weights respected: increasing one weight changes score in the expected direction (deterministic).
- **Definition of done:** score is a pure deterministic function; no random; scorer ignores bands.

### D2. `app/routing/policy.py` — `RoutingPolicy` (bands + conservative fallback)
- **File(s):** `app/routing/policy.py` (new).
- **Behavior:**
  - `bands` from `config.bands`. `bounded_band(complexity) -> str`: 0–30→native, 31–60→enrichment,
    61–100→docling. Bounds contiguous (validation in A2).
  - `low_conf_threshold[band] = {"native": config.low_conf(native), "enrichment": …, "docling": …}`.
  - `route(complexity: float, confidence: float) -> str`: compute band, then if
    `confidence < low_conf_threshold[band]`, **escalate one tier toward complex, never downgrade**:
      native→enrichment, enrichment→docling, docling→docling (bounded). Unit-test both escalation
      roles.
  - Never downgrades (spec §14). Band thresholds config, not constants (§6 §17).
- **`_cli_out` note:** D2 defines escalation as one tier; if corpus later shows over-escalation, the
  change is to `config` thresholds, not code.

---

### WAVE E — `Router` (assembly, stats, determinism)

- **File(s):** `app/routing/router.py` (new) + `RoutingStats` counters.
- **Behavior:**
  - `Router(routing_config, detectors, scorer=None, policy=None)`; default builds
    `WeightedHeuristicScorer` + `RoutingPolicy`. `scorer`/`policy` injectable for tests.
  - `route(data: bytes, detected) -> RoutingDecision | None`
    1. skip if `detected.slug not in ("pdf",)` or it is `unresolved` → return None (no routing needed).
    2. `inspector.inspect(data)` (pyfitz) → features (or None → return a valid `RoutingDecision` with
       `route="native"`, low confidence, `can't inspect` reason — never fabricate).
    3. for each detector: `can_evaluate`? → `evaluate` wrapped in try/except → collect signals.
    4. `score = scorer.score(signals, features)`.
    5. `band = policy.route(score.complexity, score.confidence)`.
    6. build `RoutingDecision`: route, complexity (int round), confidence, reasons, all signals,
       versions (`router_version`,`policy_version`,`scoring_version`, detector_versions),
       `inspection_time_ms` (measured), `bands` from config.
  - **Determinism:** no RNG, no env, uses the frozen config.
  - **Observability:** `RoutingStats` — thread-safe counters: documents routed; per-band counts;
    average / max inspection_time_ms; per-detector failure count + failure rate; score histogram
    (buckets 0-100/10); confidence histogram; missing-signal count. Exposed `.stats()` and keeps a
    `RoutingDecision.last()` / recent samples for debugging. **Note:** dedicated telemetry
    (closed-loop) is a future seam only — not built.
  - `register_detector` (C0) surfaces through router construction.
- **Test(s): `tests/test_routing_router.py`:**
  - router called with a **stub scorer/policy** produces a `RoutingDecision` → proving the composition
    path and that a race between failure-isolated detectors never raises.
  - a `Detector` raising returns `status=failed` signal, decision still produced with
    `route` from the rest.
- **Definition of done:** router is decision-only; never calls `Loaders`; stats exposed; no global
  mutable state; never emits a negative from a `failed`.

---

### WAVE F — Schema/metadata persistence (additive, old-DOM-safe)

- **File(s):** `app/parser/dom/models.py` (append only), `app/parser/parts.py` (append only).
- **Behavior:**
  - `models.py Provenance`: add `routing: Optional[RoutingDecision] = None` (import `RoutingDecision`
    from `app.routing.schema` — leaf, no cycle). Old JSON without `routing` still valid (`None`).
  - `parts.py RecoveredDocument`: add `routing: RoutingDecision | None = None` (additive).
- **`app/parser/dom/__init__.py`:** optionally re-export `RoutingDecision` for convenience (additive).
- **DoD:** old DOM fixtures (no `routing`) validate unchanged; new DOM with `provenance.routing` dumps
  round-trip.
- **Test(s): `tests/test_parser.py` + `tests/test_routing_provenance.py`: a parse through the full
  pipeline with an `auto` route records `document.provenance.routing` with all fields.

---

### WAVE G — Parser + loaders integration (surgical dispatch only)

#### G1. `app/parser/config.py` — default flips to "auto" + Routing knobs
- **File(s):** `app/parser/config.py` (edit).
- **Behavior:** `layout_backend: str = "native"` → `layout_backend: str = "auto"`; keep `"native"`/
  `"docling"` as explicit overrides (preserves ADR-007 semantics; only the default changes).
  Add a `routing: RoutingConfig` sub-config knob (from `app.routing.config`) defaulted to
  `RoutingConfig()`, plus `routing_enabled: bool = False` flag (gate; see integration note) OR a simpler
  `routing: RoutingConfig | None`. Update `ParserConfig.snapshot()` to include the routing snapshot.
  Update `test_default_layout_backend_is_native` → it must now assert `"auto"` (update the test).
- **DoD:** `default_config().layout_backend == "auto"`; explicit `"native"`/`"docling"` still work.

#### G2. `app/parser/extraction.py` — compute route after detect, before dispatch
- **Edit:** `app/parser/extraction.py`.
- Compute `route` and pass to `Loaders.load`:
  - `route = self._compute_route(data, detected)` where:
    - if `config.layout_backend == "docling"`: `route="docling"` (for pdf/images, unchanged).
    - if `config.layout_backend == "native"`: `route="native"`.
    - if `config.layout_backend == "auto"` and `detected.slug == "pdf"`: `router.route(...)`.
    - otherwise undefined/legacy: `route=None` (old path).
  - `loaders.load(detected, data, route=route)`; keep `rec` from whatever loader; propagate
    `rec.routing = decision` so the builder can persist provenance.
  - Extend the `document.parsed.v1` event payload with `route` (architecture §3 — reuses existing bus;
    no new bus). Add `route` to the `report` map.
- **DoD:** existing `native`/`docling` manual overrides produce identical behavior to today; the
  `auto` path routes only PDFs.

#### G3. `app/parser/loaders/loaders.py` — route-aware dispatch switch
- **Edit:** `app/parser/loaders/loaders.py` (only the dispatch, not `_pdf/_docx`).
- `def load(self, detected, data, *, route: str | None = None)`:
  - compat: if `config.layout_backend == "docling"` and `slug in (…pdf/png/jpg/gif/tiff)` → docling
    (unchanged). If `"native"` → native. In `auto`: `route = route or (a pdf : router decision)`.
  - inside `load`: if `route == "docling"` and `slug in (pdf, images)` → docling loader (existing);
    if `route == "enrichment"` and `slug == "pdf"` → `rec = self._pdf(…)` then
    `rec = enrichment.enrich_scanned_pages(rec, config)` (WAVE H) and set `rec.reading_order_authoritative=False`
    (native ROG keeps all blocks in order incl OCR ones);
    else → existing native per-format paths unchanged.
  - **No existing caller changes:** `route=None` falls back to old `config.layout_backend` behavior.
- **DoD:** all prior non-PDF loaders behaving unchanged; a PDF with `route="enrichment"` runs OCR on
  empty-text pages only. Flag B: this does modify `loaders.load` default signature — keep it kwarg-only.

#### G4. `app/parser/dom/builder.py` — map `RecoveredDocument.routing` → `Provenance.routing`
- **Edit:** `app/parser/dom/builder.py`. When building `Provenance`, set
  `routing=recovered.routing` if present (else None). No re-render/other change.
- **DoD:** `document.provenance.routing` populated when the route computed; forwards observability.

---

### WAVE H — Enrichment OCR (per-spec n: native + OCR empty-text pages)

- **File(s):** `app/parser/loaders/enrichment.py` (new).
- **Behavior:**
  - `def enrich_scanned_pages(rec, config, *, pages: list[int] | None = None, ocr_fn=ocr.ocr_bytes) -> recc`
    1. find pages in `rec.blocks` with zero text-block count (blocks with `.page` absent / no text).
    2. for each empty page, render EXACTLY ONE raster via fitz `get_pixmap` (only here, per
       architecture). Convert to PNG bytes.
    3. `ocr.ocr_bytes(png_bytes)` → per-line `RecoveredBlock(page=p, source="ocr", seq=…,
       confidence=engine conf, ocr_engine=ocr.engine_name())`; append to `rec.blocks`.
    4. try/except around the whole page-OCR (a failed page → recorded, continue; never crash, §11).
    5. **Not v1:** per-page *region* selectively, page-embed, re-order tables. The signature accepts an
       optional `pages` (named-arg seam for future selectivity) but v1 defaults to all empty pages.
  - `reading_order_authoritative` stays `False` for enrichment (native ROG recovers all blocks incl OCR).
  - No new OCR dependency (`app/parser/ocr.py` reused e.g. `ocr.ocr_bytes`).
- **Interfaces do not preclude future selectivity; do not build it.**
- **Test(s): `tests/test_routing_enrichment.py`:**
  - a rendered 1-page PDF with one text page + one empty (image-only) page → enrichment adds OCR blocks
    to the empty page, source="ocr", page correctly set.
  - deterministic: same bytes → same enrichment result.
  - a fully-text PDF → no enrichment needed, `rec` unchanged (no double-read).

---

### WAVE I — Feature/test infrastructure (spec §17) + regression

- **File(s):** `tests/test_routing_*.py` (the tests referenced in each wave) + a regression corpus harness.

#### I1. Detector tests (per-detector, §17)
- In each C# wave, add `tests/test_routing_detectors_*.py` exercising the specific signals +
  independence/try-except. Reference fixtures: build text/scanned/image-only PDFs via PyMuPDF helpers
  (matching existing `tests/test_docling_loader.py` `_pdf_bytes()` style).

#### I2. Scoring combinations (§17)
- In D1 tests: text-only vs scan-only vs multi-column vs table vs form rows → expected score ranges;
  missing-signal never a false-negative (test: a missing `metric_table_present` does not lower the
  complexity/confidence to "trusted").

#### I3. Routing bands + boundaries (§17)
- Test: simple text PDF → native; moderate (some images / empty page) → enrichment; scan-heavy /
  complex → docling.
- **Boundary 30/31/60/61:** hand-construct `Signal` sets that yield a complexity score exactly
  30/31/60/61 → assert the correct route. Confidence set high so no escalation kicks in, and low so
  escalation verified separately.
- Escalation: low-confidence native → enrichment; low-confidence enrichment → docling; never
  docling→enrichment.

#### I4. Missing-signal not-false-negative (§17)
- A doc with an unevaluable detector (e.g. no font info, no OCR) → `missing`; confidence drops,
  but the doc is NOT simpler, and low-confidence escalation path prevents an under-route.

#### I5. Detector-failure isolation (§17)
- Inject a stub that raises in `evaluate` → router still returns a `RoutingDecision` (never falently
  fails the doc) — covered in E.

#### I6. Determinism (§17)
- Same bytes + same config (with scorer/policy/versions frozen) → identical `RoutingDecision` JSON
  across runs; assert `signals`, `version` fields.

#### I7. Regression (§17)
- Build a small set of programmatically-decidable representative PDFs (plain, scanned, multi-column,
  tabled, image-certificate) with **expected routes** (native/enrichment/docling). Save them under
  `tests/fixtures/routing/` (generated by a script, committed once) + an assertion table. Any future
  change must not silently alter behavior vs the committed expected routes. **(Gap B: the real
  `_cli_out` corpus applies weight calibration only after this is committed — see Gap.)**

- **Final**: run the full suite `.venv/Scripts/python.exe -m pytest tests/ -q` green; Knowledge Curator
  writes `checkpoints/run/run-2026-08-06-router/checkpoint.md` + `final-report.md`.

---

## Gap flags (do NOT resolve in this plan; hand to the architect)

- **A. Image corpus path (routing scope).** The architecture routes PDFs through `FastInspector`,
  but the `_cli_out` corpus includes 2 JPGs. The plan treats routing as **PDF-only v1** and leaves
  JPG/PNG attending the existing `_image` OCR loader path (deterministic, unchanged). The architect
  should confirm whether the 2 JPGs are expected to be routed (vs images unchanged) — if image routing
  is required v1, the Inspector/scorer needs an image-capable feature path the architecture does not yet
  define.
- **B — Weight «coverage» invariant.** C-wave detectors must only emit `Signal.name` values that exist
  in `RoutingConfig.weights` (additive), else the weighted sum ignores them silently. The plan enforces
  this as a test but does not decide the exact weight value for each new signal; the regression/fixture
  corpus (`_cli_out`) is the correct place to calibrate. Architect to confirm the default weight table
  and that an unknown-signal signal should be a hard `AssertionError` or a warning.

---

## Test plan at a glance (maps to spec §17)
| # | Area | Tests |
|---|------|-------|
| 1 | Detector | per-detector (C0–C9) + isolation/try-except |
| 2 | Scoring | signal combinations → scores (I2) |
| 3 | Routing | simple→native, moderate→enrich, complex→docling (I3) |
| 4 | Boundary | 30/31/60/61 (I3) |
| 5 | Missing | no false negative (I4) |
| 6 | Detector failure | one failing detector doesn't crash (I5) |
| 7 | Determinism | same input+config → same (I6) |
| 8 | Regression | persisted corpus + expected routes (I7) |

PLAN: READY