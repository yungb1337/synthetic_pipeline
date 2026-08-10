# Checkpoint — run-2026-08-06-router (Intelligent Document Router)

**Status:** COMPLETE (2026-08-10) · **Run:** `run-2026-08-06-router` · **Suite:** 159 passed / 1 skipped
**Authority:** `docs/routing-spec.md` (ratified 2026-08-06)
**Owner:** Knowledge Curator (this checkpoint) — the orchestrator's `final-report.md` is that agent's DoD artifact, not overwritten here.

---

## Objective
Replace the static Docling-routing heuristic with an **Intelligent Document Router** that decides, per document and *before* expensive parsing, which of Native / Enrichment / Docling yields the required fidelity at the lowest cost. Independent, extensible, deterministic decision layer; no Docling-default; no hard-coded binary rules.

## Deliverable (built this run)
New **`app/routing/`** module:
- **`FastInspector`** (`inspectors.py`) — PyMuPDF `fitz.open(stream=…)` **open-without-render** (no Pixmap / image decode / OCR / Docling during inspection). Missing observations are `None`, never 0.
- **9 pluggable detectors** (`detectors/`, one per concern) — metadata / text / image / layout / ocr / table / form / reading_order / font. `name/version/can_evaluate/evaluate`; failure-isolated (a failed detector records `status="failed"`, never a negative, §11).
- **Absolute-sum complexity scorer** (`scoring.py`) — deterministic weighted sum of positive evidence, clamped [0,100]; **NOT normalized** (deviation from plan's "Normalize to [0,100]"): the `RoutingConfig` weights **ARE the band map**. Confidence = share of band-driving weight-mass measured (coverage-only; no agreement term — accepted minor).
- **3-band policy** (`policy.py` + `config.py`) — `0–30 native / 31–60 enrichment / 61–100 docling`, with **conservative escalate-on-low-confidence** (native→enrichment→docling; never downgrade, §14).
- **`RoutingDecision`** persisted additively into **`Provenance.routing`** — typed, optional (old DOMs keep `routing=None`); **versioned** (`router_version`/`policy_version`/`scoring_version` + per-detector versions + `inspection_time_ms`), so "why Docling six months ago?" is answerable from metadata (§10).
- **Enrichment band (ADR-012)** — native extraction **+ OCR of no-text-block pages** via the existing `ocr.ocr_bytes` (in-place post-pass on the native `RecoveredDocument`; one render/page; `max_pages` CPU cap; zero new OCR dependency). Closes the deferred "PDF OCR fallback for scanned pages" item.
- Integration: `app/parser/config.py` default `layout_backend` `"native"`→`"auto"` (ADR-007 amendment; manual `"native"`/`"docling"` overrides preserved); `extraction.py` computes route after detection; `loaders/loaders.py` `load(..., route=)` dispatch; `dom/builder.py` maps routing into provenance; `dom/models.py` + `parts.py` additive fields. `route` added to the `document.parsed.v1` event payload (reuses existing bus, §16).

## Per-gate artifacts (this run)
| Gate | Artifact | Verdict |
|------|----------|---------|
| Spec | `docs/routing-spec.md` | ratified 2026-08-06 (user) |
| Architecture | `architecture.md` | **ARCHITECTURE: APPROVED** — reviewer PASS (after fixes) |
| Plan | `implementation-plan.md` | **PLAN: READY** (authority-gated) |
| Engineer | `engineer-report.md` | build completed (see process note) |
| Architecture review | (in run) | **PASS** after fix round |
| Quality review | (in run) | **PASS** after fix round |
| Calibration | code (config/weights + corpus regression) | calibrated on real `test_cases` corpus |
| Regressions | `tests/test_routing_*.py` + corpus | suite **159 passed / 1 skipped** |

## Reviewer verdicts
Both the **architecture** and **quality** reviewers returned **PASS** after the fix round. Majors fixed before sign-off:
- `find_tables` page-cap (latency guard).
- **table negative-vs-missing** — a table-detection *failure* is recorded as `status="failed"`, never emitted as a valid "no table" negative (§11) — the review specifically caught this conflating case.
- **corpus-test skip-guard** — the corpus regression is safely skipped when the real corpus/fixtures are absent.

Accepted **minors** (tracked in `project_memory/questions.md` under "Tracked from run-2026-08-06-router"):
1. `FastInspector` calls `get_text("dict")` + `_est_multi_column` calls `get_text("rawdict")` per page — reuse one geometry pass.
2. A "docling" route still records `route="docling"` when the engine is unavailable and native ran — record the *executed* tier.
3. Thin per-detector test coverage for form / reading_order.
4. Confidence is coverage-only (no agreement term among strongest signals).

## Calibration (final routed table on the real `test_cases` corpus)
Weights/bands were **orchestrator-calibrated 2026-08-10** (values live in `RoutingConfig`, `app/routing/config.py`; scorer absolute-sum in `scoring.py`):
- **Scanned docs** → **Enrichment (OCR)** — the scan cluster (`scanned_page_probability` 15 + `ocr_required` 18 + `low_char_density` 8 + `low_text_ratio` 12 + `image_density` 5 ≈ 58 maxed) caps **below** the 61 Docling threshold, so a purely-scanned doc lands at the cheapest tier that fixes it (per spec §5/§7), not whole-document Docling.
- **Complex-academic** (e.g. MDPI electronics) → **Docling** — genuine layout complexity (columns `/reading_order` 25 + `/multi_column` 15 + tables 12) accumulates **past 61**. **Known limitation:** layout/reading-order/multi-column detectors are pinned so complex-academic *currently* lands at Enrichment more often than desired; refinement is a tracked follow-up (over-flagging simple text if weighted harder).
- **Simple text** → **native** — low complexity, high confidence (measured zeros don't lower confidence).
- `layout_complexity` (20) kept LIGHT / `block_fragmentation` (0.0) because raw-geometry signals over-flag clean multi-paragraph text.

## Process note (why the build diverged from the plan)
The **engineer subagent hit repeated server errors** and was interrupted; the **orchestrator completed the build directly**, then applied the **CALIBRATION + review-fix round** itself. Orchestrator-applied changes include: the **scoring.py absolute-sum** change, the **calibrated weights in config.py**, the **corpus-regression test skip-guard**, and the reviewer-driven **find_tables page-cap** + **table negative-vs-missing** fixes. Note for future runs: build-stage resilience for subagents under repeated tool/server failures; keep calibration and review-fix ownership explicit.

## ADRs added / amended this run
- **ADR-007 AMENDMENT** — Docling default flips `"native"`→`"auto"` (auto-router); `"native"`/`"docling"` manual overrides preserved with identical semantics.
- **ADR-011** — Intelligent Document Router module: separate, deterministic, explainable decision layer (architecture, detector contract, scoring abstraction, policy/bands, determinism, versioning, persistence, enrichment band, diagnostics).
- **ADR-012** — OCR of scanned PDF pages (Enrichment band); closes the deferred "PDF OCR fallback for scanned pages" question.

All appended in `project_memory/architecture_decisions.md` (by the architect, retained).

## Tracked follow-ups (see `project_memory/questions.md`)
- Refine layout/reading-order/multi-column detectors so complex-academic docs reliably reach Docling without over-flagging simple text (currently pinned at Enrichment).
- The 4 accepted reviewer minors above (one-geometry-pass, executed-tier recording, form/reading_order test coverage, agreement-based confidence).
- Hindi/multilingual (Devanagari) OCR — current backend (rapidocr) default is EN/zh; tracked engine decision.

## Unmet DoD (owned by the orchestrator)
**`final-report.md`** under `checkpoints/run/run-2026-08-06-router/` is the orchestrator's duty — it is **NOT** written here (this checkpoint is the Knowledge Curator's artifact). Updating `Project Scope`/state is orchestration's job, not mine.