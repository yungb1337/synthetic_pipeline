# Intelligent Document Routing Engine — Specification

**Status:** ratified by user (2026-08-06) as the run authority for `run-2026-08-06-router`.
**Copy of record for the requirements.** The org executes against this; ADRs record decisions.

---

## 0. Objective

Replace the static Docling-routing heuristics with an **Intelligent Document Router** that decides, per document and *before* expensive processing, which extraction strategy can produce the required fidelity at the lowest cost.

- Use the cheapest pipeline that reliably yields the needed fidelity.
- Do **not** make Docling the default for every document.
- Do **not** reduce routing to a few hard-coded binary rules (`if has_images: docling`).
- The router is an independent, extensible decision layer that can evolve from deterministic
  heuristics into scoring/learned routing without changing the downstream extraction pipeline.

## 2. High-level architecture

```
            Document
               │
               ▼
       Lightweight Inspector  ──►  Feature Extraction  ──►  Routing Engine
                                                              │  ├── Native Parser
                                                              ├── Enriched Pipeline
                                                              └── Docling
                                                              │
                                                              ▼
                                                          Canonical DOM
```

Routing sits before expensive parsing/layout. Downstream must not know how the decision was made.

## 3. Separate inspection from routing

- **Inspector** answers only *"What can I cheaply observe?"* — produces normalized features/signals.
  No routing decisions.
- **Router** answers only *"Given these signals, which strategy processes this?"*
- The same inspection features will later serve analytics, monitoring, debugging, quality
  prediction, ML routing, cost estimation — so the inspector must be decision-free.

## 4. Lightweight inspection signals

Gather where available; **missing signals are represented explicitly (None/absent), never silently
treated as positive or negative evidence.**

- **Document:** MIME/type, file size, page count, PDF version, encryption, producer/creator,
  tagged-PDF, outline/bookmarks, structural metadata.
- **Text:** whether embedded text exists, text density, chars/page, text-to-page ratio,
  extractable char count, suspicious/invalid-char ratio, Unicode quality, fragmentation,
  extraction confidence.
- **Image:** image count, images/page, coverage, full-page-image probability, raster/vector
  distribution, scanned-page probability.
- **Layout:** multi-column probability, layout complexity, text-block distribution,
  reading-order ambiguity, overlapping regions, rotated text, header/footer repetition,
  spatial fragmentation.
- **Structural/content:** table probability, form probability, figure/image density,
  list/section structure, repeated page templates.
- **Typography:** font count, font diversity, font embedding, unusual font usage.

## 5. Detector architecture

Pluggable detectors. **"Do not implement all signals directly inside the router."**

```
Inspector
  ├── MetadataDetector   ├── TextDetector        ├── ImageDetector
  ├── LayoutDetector     ├── OCRDetector         ├── TableDetector
  ├── FormDetector       ├── ReadingOrder        ├── FontDetector
```

Each detector:
- decides whether it can evaluate the doc,
- extracts its feature(s),
- returns confidence/quality where appropriate,
- returns structured results,
- **fails independently** without bringing down routing (a failure is recorded, not a negative signal).

## 6. Score / confidence / routing policy

- **Complexity score 0–100** (normalized) from detector evidence; weights **configurable**, not
  scattered in code.
- **Confidence** separately captures certainty in the assessment. Low-confidence inspection must have
  a **defined fallback policy** — avoid aggressive routing on weak evidence.
  `{complexity:82, confidence:0.94}` vs `{complexity:82, confidence:0.42}` may route differently.
- **Tiers (config threads, not hardcoded):** 0–30 → Native · 31–60 → Enrichment · 61–100 → Docling.
- Scoring sits behind an abstraction so it can later be heuristics / rules / statistical / ML without
  touching the pipeline.

## 7. Enrichment band (31–60)

Localized complexity (isolated tables, a few scanned pages, localized image-heavy pages, moderate
reading-order issues) that does not justify whole-document Docling. v1 keeps simple; interfaces must
not preclude future page/region-level selectivity (but don't build page-level orchestration now).

## 8. Explainability

Every decision carries structured reasons generated from detector results — never only `{route}`:
```json
{ "route": "docling", "complexity_score": 78, "confidence": 0.91,
  "reasons": ["high scanned-page probability", "multi-column layout detected", "low reading-order confidence"] }
```

## 9. Persist routing metadata (provenance)

At minimum: route, complexity score, confidence, detector results, decision reasons, inspection
latency, **router version, policy version, detector versions**. Travels with the document through the
pipeline. Required for auditing, debugging, failure/performance/cost analysis, regression testing,
routing-quality evaluation, future model training.

## 10. Version the decision system

Version Router, Policy, Detectors, Scoring config independently. A decision today may differ after
the algorithm changes — must be able to answer "why Docling six months ago?" on versioned metadata.

## 11. Failure handling

- A detector failure must not fail the document; record the failure and continue routing.
- **Distinguish "no table detected" from "table detection failed"** — never turn a failure into a
  valid negative signal.

## 12. Determinism

Identical input + identical config/version ⇒ identical decision. No hidden randomness,
non-deterministic thresholds, environment-dependent behavior, implicit global state.

## 13. Observability

Measure at least: docs inspected; routed native/enrich/docling; avg inspection latency; detector
latency; detector failure rate; score distribution; confidence distribution. Allow future closed-loop
measurement routing decision → extraction quality → downstream validation.

## 14. Tradeoffs (documented)

- **Accuracy vs compute:** Docling on everything improves fidelity but raises CPU/GPU, latency, cost,
  throughput pressure. Router trades small routing complexity for large compute savings.
- **False pos (simple→Docling) is safe/wasteful; false neg (complex→Native) reduces fidelity.**
  Initial policy is **conservative toward complex docs** — if unsure a doc is safe for native,
  prefer the more capable pipeline.
- **Inspection cost:** the inspector must stay far cheaper than the processing it avoids; do not
  introduce expensive ML into the "fast" inspection phase.
- **Complexity ≠ guaranteed Docling benefit:** allow future feedback from downstream validation.

## 15. Preserve the pipeline contract

No routing complexity leaks into the rest of the parser. Native / enrichment / Docling / future
engines all converge on the **same canonical DOM**. Downstream consumers never care which engine ran.

## 16. Do not over-engineer v1

Establish the correct interfaces, separation, deterministic scoring, policy, metadata, observability
— and nothing else. No ML model, no distributed infra, no plugin-discovery framework, no external DB,
no elaborate event bus, no page-level orchestration **unless the repo genuinely requires it.**
Simple implementation, strong architecture.

## 17. Testing

- Detector: each independently testable.
- Scoring: signal combinations → expected scores.
- Routing: simple PDF→native; moderate→enrichment; complex→Docling.
- Boundary: score 30/31/60/61.
- Missing-signals: don't become false negatives.
- Detector failure: one detector failing doesn't crash routing.
- Determinism: same input+config → same decision.
- Regression: persisted representative docs + expected decisions; future changes must not silently
  alter behavior.

## 18. Final structural contract

```
Detector   "What do I observe?"
Inspector  "What features can I cheaply establish?"
Router     "What pipeline should process this?"
Pipeline   "How should I process it?"
Canonical DOM
```

Extraction pipeline must **not** contain routing logic. Router must **not** contain extraction logic.
Detectors must **not** contain pipeline-execution logic. Add a detector / change weights / change
thresholds / add a backend / replace scoring with a learned model — all without rewriting the rest.

**Do not modify unrelated parts of the repo. Preserve existing contracts and behavior for documents
that do not require routing changes.**