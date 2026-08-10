---
run: run-2026-08-06-router
authority: docs/routing-spec.md (ratified 2026-08-06)
status: design
---

# ARCHITECTURE: APPROVED

Intelligent Document Routing Engine for `run-2026-08-06-router`. Verdict after full trade-off
review below: the design is a **strong, v1-simple architecture** that honors every clause of
`docs/routing-spec.md` (§1–§18), preserves the canonical-DOM contract, and changes no existing
document's behavior when routing decision is Native. Approved with the reservation that routing
band weights and confidence thresholds are **initial guesses** to be calibrated against the real
verification corpus (`_cli_out`, 12 PDFs + 2 JPGs) before they become durable policy.

---

# 1. High-level view (spec §2 → §18)

```
             bytes + Detected(type)
                     │
                     ▼
        ┌─────────────────────────────┐  FastInspector (PyMuPDF OPEN-ONLY, no render)
        │  inspectors.py  FastInspector│  gather Decision-free raw features from the bytes
        └───────────────┬─────────────┘
                        │ InspectorFeatures (metadata, text-rows, images, pdf meta…)
                        ▼
        ┌─────────────────────────────┐  detectors/  (one per concern)
        │  per-detector evaluate(feats)│  can_evaluate / evaluate -> Signal[]
        └───────────────┬─────────────┘
                        ▼  Signals[] (ok / failed / missing)
        ┌─────────────────────────────┐  Scorer abstraction (weighted heuristic v1;
        │  router.Scorer.route(signals)│  seam for rules/stats/ML)
        └───────────────┬─────────────┘
                        ▼  complexity 0-100 · confidence 0-1 · band · reasons · versions
        ┌─────────────────────────────┐  Router.policy.band(score)+fallback(spec §6, §14)
                        ▼  RoutingDecision
                        ▼
   Extraction dispatch  ──► Native │ Enriched │ Docling
                                     │           │           │
                                     ▼           ▼           ▼
                             (existing)  Enrichment   (existing docling)
                             native      loader       loader
                                     └──────────┬──────────┘
                                                ▼
                                          RecoveredDocument
                                                │ (builder, proven) + routing decision
                                                ▼
                                           Canonical DOM
```

- **Inspector answers "What can I cheaply observe?"** (spec §3): raw, *decision-free* features.
- **Detectors answer "What do I note?"** (spec §5). Decision-free, failure-isolated.
- **Scorer answers "Given notes, complexity/confidence?"** (spec §6) behind an abstraction.
- **Router+RPolicy answers "Which pipeline processes this?"** (spec §18). Aggregate + version + explain.
- **Extraction dispatch answers "How do I process it?"** (executes the decision).
- All converge on the **same canonical DOM** (spec §15). No routing leaks downstream.

---

# 2. New module layout — `app/routing/`

```
app/routing/
  __init__.py
  schema.py            # Signal, DetectorSignal, RoutingSignals, RoutingDecision (pydantic, LEAF —
                       #  imports nothing from app; parser/dom may import it) 
  config.py            # RoutingConfig (immutable snapshot): bands, weights, versions, fallback
  detectors/
    base.py           # Detector ABC: name/version/can_evaluate/evaluate -> list[Signal]
    meta_detector.py    name/version/can_evaluate/evaluate -> MetadataSignal
    text_detector.py    name/version/can_evaluate/evaluate -> TextSignal
    image_detector.py   name/version/can_evaluate/evaluate -> ImageSignal
    layout_detector.py
    ocr_detector.py
    table_detector.py
    form_detector.py
    reading_order_detector.py
    font_detector.py
    __init__.py         # registry: DETECTOR_NAMES -> factory (pluggable, additive)
  inspectors.py      # FastInspector: cheap PyMuPDF open-without-render feature pass
  scoring.py          # Scorer ABC (Protocol) + WeightedHeuristicScorer (v1 impl)
  router.py           # Router(policy, scorer, detectors): route() -> RoutingDecision
  policy.py           # banding thresholds, confidence fallback strategy
```

**Facts** this layout satisfies from the authority: §16 ("establish correct interfaces,
separation, deterministic scoring, policy, metadata, observability — nothing else"; no ML model /
distributed infra / plugin-discovery framework / external DB / page-level orchestration), §3
(Inspector decision-free, Router decides), §5 (pluggable detectors, one per concern, additive),
§6 (scoring behind abstraction, weighted config, tiers-as-config).

`app/routing/schema.py` is a **leaf** model module (imports only pydantic) so that
`app/parser/dom/models.py` can import `RoutingDecision` into `Provenance.routing` with *no*
circular dependency and *no* parser->router coupling beyond a plain type reference.

---

# 3. Detector contract (spec §5)

```python
# app/routing/detectors/base.py
class Detector(ABC):
    name: str            # stable id e.g. "text"
    version: str         # e.g. "1.0.0"; independently versionable (spec §10)
    def can_evaluate(self, feats: InspectorFeatures) -> bool
        """Cheap predicate: can this detector's signal be evaluated at all? Returns False
        (recorded as 'not-applicable', not 'negative evidence') when the doc can't support it.""" 
    def evaluate(self, feats: InspectorFeatures) -> DetectorResult:
        """Decision-free observation. Returns ONE result with >=0 Signals.
        MUST NOT throw: catch, set ok=ok=False on a Signal, re-raise never (spec §11)."""
```

`DetectorResult`:
```python
@dataclass
class DetectorResult:
    detector: str
    version: str
    status: str            # "ok" | "failed" | "not_applicable"
    error: str | None = None
    signals: list[Signal] = field(default_factory=list)   # zero signals on failure
```

`Signal` (the unit of evidence, spec §4):
```python
class Signal(BaseModel):
    detector: str
    version: str
    name: str               # stable, e.g. "metric_page_char_density_none"
    value: Optional[float | bool | str] = None   # MISSING = None, never 0/False defaulted (spec §4)
    confidence: Optional[float] = None           # 0..1; None = not established
    evidence: Optional[str] = None               # short human-readable reason (spec §8 explainability)
    status: str = "ok"                           # "ok" | "failed" | "missing" | "not_applicable"
```
- A **scanned-page probability**, a **cross-column probability**, a **digit/ratio…**, etc. each
  become a `Signal` (one concern → often many signals). A missing/unevaluable observation →
  `status="missing"`, `value=None` — never coerced into a false 0/False ("negative") heuristic
  (spec §4, §11).
- Failure is **recorded, not a negative signal** (§11): failure ⇒ `status="failed"`, no signal
  that says "no table".

---

## 4. `InspectorFeatures` — the decision-free feature pass (spec §4 + §3)

`FastInspector` opens the PDF with **PyMuPDF `fitz.open(stream=…, filetype="pdf")` ONLY — no
rendering (no Pixmap / no image decode)**: metadata/outline/encryption + per-page `get_text`/`get_images`
/`page_char_density` (raster dims) are all available without rendering; this is what keeps the
inspector **far cheaper than the processing it avoids** (§14 "Inspection cost").

**Facts** about what PyMuPDF exposes without rendering: document-level metadata (the `metadata`
dict: format, encryption, creator, producer, title, subject, and tag where available), outline
(`doc.get_toc()`), page count, and, per page, `page.get_text("rawdict")` text spans + embedded
fonts + `page.get_images(full=True)` image refs — all **without a Pixmap render**. OCR / Docling are
never invoked during inspection — inspection is a cheap metadata + text-geometry read only.
(Inference: per-page text-block count + ratio of image-only pages are real, cheap signals.)

```python
@dataclass
class InspectorFeatures:
    # metadata-level
    mime_slug: str; declared_extension: str; pdf_version: str | None
    encrypted: bool | None; producer: str | None; creator: str
    outline: bool; tag: bool | None
    page_count: int
    page_dims: dict[int, (w,h)]
    # text
    pages_char_count: dict[int, int]; chars_per_page: list[float]
    text_ratio: float | None; fragment_count: int | None
    # image
    image_count: int; images_per_page: list[int]; covered_pages: int
    full_image_pages: list[int]   # pages with ~full-area image -> likely scanned
    # layout hints (heuristic, cheap)
    est_multi_column_pages: list[int]   # block bbox x-overlap clustering, v1 heuristic
    block_count_per_page: list[int]     # spatial fragmentation
    # structural
    detected_tables: int | None         # PyMuPDF find_tables presence (cheap), else None
```
Inspector computes these into a small `InspectorFeatures` dataclass (deterministic, no hidden
state). Detector **evaluate()** methods read `InspectorFeatures` and emit `Signal`s; a detector
independent of the inspector cannot read bytes again (one read) and cannot render.
**Decision-free guarantee (Fact, from spec):** Inspector and detectors never read `config.layout_backend`
nor any policy/score — they only **observe**.

---

## 5. Scoring abstraction + policy (spec §6, §8, §14)

Scoring and quantization live behind `Scorer` so scoring can become heuristic→statistical→ML without
touching the pipeline (§6):

```python
# app/routing/scoring.py
class Scorer(Protocol):
    def score(self, signals: list[Signal], inspector: InspectorFeatures) -> Score:
        """Returns complexity 0.0..100.0 + confidence 0.0..1.0 + a reasons list."""
        ...
```
v1 impl `WeightedHeuristicScorer` — deterministic:
- Each candidate signal carries a **weight** from `RoutingConfig.weights[signal_name]` (config file,
  *not* hardcoded, §6).
- Each signal makes a bounded, normalized contribution toward complexity; weighted sum → clamp [0,100].
- **Confidence** derives from (a) the share of signals critical to the band decision that are
  `missing`/`failed` (more missing ⇒ less to trust ⇒ lower confidence), and (b) agreement among the
  strongest signals.
- **`reasons`** are collected from the strongest positive signals + the band-breaking signals
  (§8, e.g. `"high scanned-page probability (0.9)"`).
- A `Scorer` gets no information about the output band — the router applies band thresholds
  outside the scorer (spec §8, §6 separation).

`policy.py`: band thresholds are **config**, not constants (spec §6 §17):
```python
class RoutingPolicy:
    bands: list[tuple[int,int,str]] = [(0,30,"native"),(31,60,"enrichment"),(61,100,"docling")]
    native_low_confidence_threshold: float   # e.g. 0.50
    enrichment_low_confidence_threshold: float # e.g. 0.35
    # conservative toward complexity (spec §14)
    def route(self, complexity:float, confidence:float) -> str:
        band = bounded_band(complexity)
        if confidence < low_conf_threshold[band]:
            band = escalate_closest_band(band)     # native->enrichment, enrichment->docling
        return band
```
Decision: **escalate toward complex on low confidence** — per §14 "conservative toward complex
docs: if unsure a doc is safe for native, prefer the more capable pipeline". A confident plain
native PDF stays low-cost; an ambiguous scanned doc escalates. False-positive (simple→Docling) is
safe/wasteful; false-negative (complex→Native) reduces fidelity — policy escalates on uncertainty,
never downgrades.

`Router` assembles the decision with versioning (§10): `router_version`, `policy_version`,
`detector_versions`, `scorer_version` all stamped into the decision.

---

## 6. RoutingDecision (spec §8 + §9, §10)

```python
# app/routing/schema.py  (leaf — imports only pydantic)
class RoutingDecision(BaseModel):
    route: str                     # "native" | "enrichment" | "docling"
    complexity_score: int          # 0..100
    confidence: float              # 0..1
    reasons: list[str]             # human-readable, from detector+scorer (§8)
    signals: list[Signal]          # full evidence, incl. failures (§17)
    router_version: str
    policy_version: str
    scoring_version: str
    detector_versions: dict[str, str]  # detector -> version (independently version, §10)
    inspection_time_ms: float
    bands: dict[str, tuple[int, int]]  # band -> (lo,hi) repr for regression/audit (§6)
```
It is deterministic: same bytes + same config+version ⇒ same decision (§12) — no hidden randomness.

---

## 7. Integration into extraction (surgical, backward-compatible)

The **only** edits in `app/parser/*` are:

1. `app/parser/config.py` — `layout_backend: str = "native"` → `layout_backend: str = "auto"`,
   keep `"native"`/`"docling"` as explicit overrides; add `Routing` sub-config knobs (see §8).
   (This preserves ADR-007's existing "native"/"docling" manual overrides; the *default* changes.)
2. `app/parser/dom/models.py` — add to `Provenance`:
   ```python
   routing: Optional["RoutingDecision"] = None    # additive; old DOMs keep validating (None)
   ```
   (import `RoutingDecision` from `app.routing.schema` — leaf, no cycle.) This is the §9 mutation.
3. `app/parser/parts.py` — add to `RecoveredDocument`:
   ```python
   routing: RoutingDecision | None = None      # additive
   ```    
   Builder maps it into `Provenance.routing`.
4. `app/parser/extraction.py` — compute the route **after** `detection.detect` and **before**
   `self.loaders.load`:
   ```python
   route = self._compute_route(data, detected)      # layout_backend override OR router
   try:
       rec = self.loaders.load(detected, data, route=route)
   ```
5. `app/parser/loaders/loaders.py` — **only the dispatch switch** becomes route-aware (no
   `_pdf/_docx…` changes):
   - `layout_backend == "docling"` (old manual override) → `route="docling"` for pdf/images (unchanged).
   - `layout_backend == "native"` (old manual override) → `route="native"`.
   - `layout_backend == "auto"` → `route = router.route(...)`.
   - Inside `load`: `if route == "docling" and slug in (...) : docling_loader…`
     `if route == "enrichment" and slug in PDF: enriched pdf path`
     else existing native paths unchanged.
6. `Loaders.load(detected, data, *, route: str | None = None)` — new optional kwarg. **No existing
   caller changes**: default `route=None` falls back to the old behavior decided purely from
   `config.layout_backend` (selected for the legacy "native"/"docling" modes).

A **code-coupling note (Fact):** the docling branch already lives inside `loaders.load` — so routing
via a `route` kwarg is the *minimal-diff* seam; we do not pull the docling block out to the Extractor.
The new enrichment branch reuses the existing native `_pdf` loader and applies the OCR post-pass on
its result.

**Where the router is instantiated:** `Extractor.__init__` builds a `Router` singleton (lazy) and
passes it to `Loaders` (or exposes `.route`). Detector/Scorer instances come from an injectable
factory so tests can substitute a fixed scorer.

Integration is directionally additive: routing metadata is written into `RecoveredDocument.routing` →
builder → `Provenance.routing`.

**old-DOM guard (Fact):** old `Document` JSON has no `Provenance.routing`; adding `routing=None`
default keeps old documents valid (§16 says "old DOMs must keep validating").

---

## 8. Enrichment band v1 (spec §7, run-brief "Enrichment v1, simple")

Enrichment = **native extraction + OCR of pages that have no text blocks**, delivered as a single
**in-place post-pass on the native RecoveredDocument** (NOT a second full read; page-level
orchestration is **explicitly out of v1** §16).

```python
# app/parser/loaders/enrichment.py (new, reuses ocr)
def enrich_scanned_pages(rec: RecoveredDocument, config: ParserConfig,
                          render_fn=... ) -> RecoveredDocument:
    """After native PDF extraction, OCR pages with zero text blocks."""
    # (1) find pages with no text blocks in rec (blocks with .page == p)
    # (2) render each such page to a raster via fitz ("get_pixmap") — ONE render per page, only here
    # (3) ocr_bytes(...) beats into RecoveredBlock(page=p, ..., source="ocr"); append to rec.blocks
    #     (confidence = engine conf; a failed page -> recorded, never a negative)
    # (4) NOT v1: per-page *region* selectivity, page-re-embed, page table pass
```
- `rec.reading_order_authoritative` stays `False` for enrichment (native `reading_order.recover_reading_order`
  reorders all blocks incl. the OCR ones — spec-consistent; page-level orchestration is out of v1).
- The **detach of `page_download` and OCR is direct** using the existing dangerous-but-working `ocr.ocr_bytes`
  (`app/parser/ocr.py`, ADR-#4 image path). **No new OCR dependency.**
- **State in provenance:** `RecoveredDocument.blocks` source="ocr"; the builder already sets
  `ocr_engine`/`oct_level` from block-level OCR. So enrichment is observable without model changes.
- **Interfaces don't preclude page/region selectivity:** `enrich_scanned_pages` accepts a `page: list[int] | None`
  target pages + `ocr` injection seam; v1 ships the "all empty pages" default only, and the seam
  signature is backward-chosen so a future `(pages, region)` selector is a named-arg extension, not a change.

---

## 9. Failure isolation + determinism + observability (spec §11 + §12 + §13)

- **Isolation:** each detector `evaluate` is wrapped by the router in try/except that records the
  failure (`Signal(status="failed")`) and continues — one failing detector never fails the document,
  never fails routing, never becomes a negative (spec §11). Enrichment per-page OCR is also
  try/except (page fails → recorded, continue).
- **Determinism (Fact):** routing is a pure function of `(bytes, Detection, RoutingConfig snapshot)`.
  No randomness; thresholds/config only from config; no random seed, no env-behavior, no implicit
  global (spec §12). All scoring/versions frozen into the decision for reproducibility.
- **Observability (spec §13):** `RoutingDecision` carries detector versions + reasons + `inspection_time_ms`.
  The router exposes an in-memory `RoutingStats` counter (docs inspected / routed native / enrichment /
  docling, per-detector failures, score & confidence histograms) and document in the run spec that
  dedicated telemetry (closed-loop: routing -> extraction quality -> validation) is a **future seam**
  (seam, not built; §13 spec. §16).
  The router also preserves the pipeline's existing metric `document.parsed.v1` event - we extend
  the event payload with the `route` so no new bus is built (spec §16 "no elaborate event bus").

---

# 10. Trade-off review (Decision Challenge folded in)

Every decision below lists **≥2 real alternatives**, a scoring, the chosen, and "what would change
my mind".

### D1. Detector interface shape
- **(a) ABC class per concern** (`Detector` with `can_evaluate`/`evaluate`), registered in a list.
- **(b) plain callable-functions registry** (dict of name→func).
- **(c) plugin-discovery framework** (scan/importlib hook).
- Scoring dims: cleanness (v1 per §16), extensibility without rewriting, testability, failure
  isolation, build simplicity.
- **Chosen (a):** one class per concern matches "one detector per concern" §5, gives natural
  `name/version` + `can_evaluate`, allows independent unit tests (§17), and isolates failure at a
  method boundary. Registry is a **plain list** (additive import in `__init__`), not a plugin
  framework — that satisfies §16 (no plugin-discovery infra). Detector `can_evaluate` lets detector
  opt-out per doc cheaply (§5).
- **What would change my mind:** if adding a detector repeatedly required touching two other files
  (a real defect), switch to a name→ctor registry (still no plugin framework). If the set of
  detectors grows >10 with cross-detector weights, a small decorator-based registry would win.

### D2. Where scoring lives
- **Options:** (a) scoring **inside Router**; (b) **separate `Scorer` behind a `Protocol`**
  (v1 = `WeightedHeuristicScorer`); (c) scoring computed per-detector and merely summed.
- Dims: obey §6 ("behind an abstraction so it can later be heuristics/stats/ML"), mono-modular
  seam, low coupling, testability, not over-engineering (a `Scorer` ABC adds 1 indirection but buys
  the future swap §6 promises).
- **Chosen (b):** `Scorer.scoring→(complexity, confidence)` is behind an ABC; Router owns policy
  (band thresholds) separately from scoring. Replaces the weighted sum → a `LearnedScorer` later
  with **no pipeline change** — exactly §6's promise.
- **What would change:** if the future ML model needs features the base inspector cannot cheaply
  yield, the `Scorer` interface expands to accept a richer feature view (additive). If v1 proved
  the heuristic score's confidence is mostly noise, we would simplify to a fixed conservative
  default rather than keep a false-confidence signal.

### D3. How the router hooks into extraction
- **Options:** (a) **Extractor computes `route`, passes to `Loaders.load`** (the `route` dispatch
  seam above); (b) a Router-wrapped `Loader` that stacks before native/docling; (c) router logic
  inside `detection`.
- Dims: surgical diffs, preserve existing contracts, no routing leak into the pipeline, no
  pipeline knowledge into the router, one single native/document path, backward compat.
- **Chosen (a).** `extraction` already holds the `PG.content-type`/`detected`; passing an optional
  `route` kwarg to `Loaders.load` (default preserved) is the **minimal surface** — the docling branch
  already lives inside `loaders.py`, so the seam reuses it, rather than duplicating docling logic.
  `detection` stays pure (type-only), and the router never touches `Loaders`.
- **What would change my mind:** if `loaders.py` grew a second routing-consuming branch and the
  `route` kwarg became a cross-cutting concern, extraction would move to a small `PipelineSetup`
  that selects among `Loader` instances (a cleaner conveyor seam). Deferred — not needed v1.

### D4. How enrichment (scanned-page OCR) is realized
- **Options:** (a) **in-place post-process on the native `RecoveredDocument`** (`OCR + append blocks`);
  (b) a **full second read** through a new "enrichment loader" that re-parses the PDF; (c)
  page-level landing/orchestration.
- Dims: §16 (do not build page-level orchestration now), cost (one read vs two reads), fidelity,
  seam for future selectivity, keep reading-order single-pass.
- **Chosen (a):** native already produced blocks; only **empty-text pages** get OCR + append (single
  read, single DOM pass). Interface accepts an optional page/region selector as a named-arg seam for
  the future, but **no page-level orchestration now** (§16). Rejected (b): a full second read of the
  PDF — violates the module's "one read" invariant. Rejected (c): page-level orchestration — 
  explicitly out of v1 and §16.
- **What would change my mind:** if a measured corpus showed most enrichment docs are *fully* scanned
  (so the right call is Docling for them), the OCR post-pass stays only for the native/enrichment
  cases and the router becomes the arbiter (detector/scoring changes — not the integration).

### D5. Where routing metadata is persisted
- **Options:** (a) **new typed `Provenance.routing: RoutingDecision`** nested Pydantic field via the
  leaf `app/routing/schema.py`; (b) a generic `dict` field on `Provenance`; (c) a separate store.
- Dims: spec §9 (travels with document, versioned, auditable, additive), old-DOM validation, minimal
  parser diff, no parser→router coupling beyond a plain type reference, browsable provenance.
- **Chosen (a):** a strongly-typed field is self-documenting and validates (unlike a raw
  dict), and carries the versioned decision JSON. Import direction is one-way (schema is a leaf;
  parser imports it) → no circular dep. Old DOMs get `routing=None` (additive, spec §16).
- **What would change my mind:** if `routing` needed to grow many independently-versioned variants,
  a schema-free `dict` in this one field would be the fallback; v1 keeps the typed field — more
  faithful and less inventable.

### D6. Low-confidence fallback policy
- **Options:** (a) **escalate one tier on low confidence** (native→enrichment, enrichment→docling;
  never downgrade); (b) on *any* low confidence default to Docling; (c) ignore confidence & route on
  score only.
- Dims: meets §6 (confidence separate from score), §14 (conservative toward complex docs), cost
  (don't over-send simple docs to Docling), predictability.
- **Chosen (a):** escalate one tier toward complex on low confidence, bounded — a confident plain
  native PDF stays low-cost; an ambiguous scanned doc escalates. This matches §14 exactly and is
  defensible in audits. 
- **What would change:** if the confidence model is uncalibrated (corpus shows too-high confidence →
  wrong native on scan-heavy PDFs), we would raise thresholds or set the low-confidence band fallback
  to a fixed policy. If data shows Docling rarely helps borderline docs, we suspend escalation.

---

# 11. Relationship to existing ADRs & deferred seams

- **Amends** ADR-007 (this run) — see below (ADR-007 amendment). ADR-007's `native`/`docling`
  override semantics + lazy docling loader are preserved; only the *default* flips to auto-router.
- **New** ADR-011 — Document Router (this doc). **New** ADR-012 — OCR of scanned PDF pages for the
  enrichment band (closes the deferred "PDF OCR fallback for scanned pages" item in
  `project_memory/questions.md`).
- **Reserved, future seams (NOT built v1, §16):** page/region-selective enrichment;
  page-level orchestration; a `LearnedScorer`; closed-loop routing-quality feedback event; a
  detector-based plugin registry; batching for the inspection pass; per-document cost-estimation.
  Each is alluded to in the interface above so v1 code does not close the door.

## 12. What we are NOT doing (spec §16, brief constraints)
- No ML model in the routing fast path. No distributed infra. No plugin-discovery framework. No
  external DB. No page-level orchestration. No change to existing loaders beyond the route kwarg
  dispatch. No change to the native heading heuristic or chunking (tracked follow-ups).
- The **native benefits** (conservative cost, determinism, faithfulness, provenance, on-prem) remain
  the default for the bulk of real corpora: auto-native for plain text PDFs.

---

# Verdict

**ARCHITECTURE: APPROVED.** The design satisfies every clause of `docs/routing-spec.md`, integrates
surgically into extraction, keeps v1 strong-and-simple (§16), and preserves the canonical-DOM trust
boundary. Reservation: detector weights / band thresholds / low-confidence fallback values are
initial and MUST be calibrated against the real `_cli_out` verification corpus (12 PDFs + 2 JPGs) —
the architecture makes that a `RoutingConfig` change, not a code change.

Labels: all class / interface / layering claims above are **Fact**. Performance claims about PyMuPDF
"text without render" are **Fact** for "does not rasterize", and **Research** for exact speed on the
4 GB RTX 3050 machine. Everything tied to which band picks which doc is **Inference** until
regression tests + the corpus measure it.