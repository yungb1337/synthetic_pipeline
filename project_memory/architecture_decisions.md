---
name: parser-scope-and-decisions
description: Accepted scoping and key decisions for the first module (the parser / document extraction pipeline), derived from SYN1-4
metadata:
  type: project
---

# Parser Module — Accepted Decisions & Scope (Module #1)

**Decision base:** SYN4 (the conversation that redesigned the pipeline as a single-pass extraction → Canonical Document Object), SYSTEMAL from the project brief (modular monolith, event-driven, idempotent, observable, no premature microservices).

## The one sentence
The parser = **Document Extraction Pipeline**: detect file type → load once → run parallel extractors (text / layout / OCR / tables / images / metadata / coordinates / annotations) → build a canonical **Document Object Model** → serialize + persist. Output must be parser-independent so all downstream (normalize, chunk, embed, KG) never touch the original file.

## Accepted decisions (Fact — from SYN1-4)
1. **Canonical DOM object** between parse and everything downstream (`Document → Metadata, Pages → Blocks → Paragraphs/Table/Image/Annotation`, coordinates, references, version).
2. **Layout is an extractor inside the pipeline**, not a separate engine. Do not read the file twice.
3. **Reading Order Graph** = in-memory directed graph (`block → next block`). No Neo4j.
4. **OCR is on-demand** by detected type (scanned PDF / image) and recorded (deskew → denoise → contrast → OCR model → text + confidence).
5. **Tables are first-class** (grid, rows, columns, headers, cell types); loss without them.
6. **Images are extracted + stored + referenced**, NOT analyzed in MVP (GPU + latency).
7. **Modular monolith** (FastAPI backend + module packages + worker pools). Modules = responsibilities, not network boundaries.
8. **Idempotent** jobs; **versioned** outputs (`parser:vN`), full lineage retained; **immutable object storage** for raw + parsed.

## In scope for THIS milestone
- File type detection (magic bytes + container + content sniff + extension last).
- Loaders: PDF (text+layout+tables+images), DOCX, XLSX/CSV/TSV, HTML/Markdown, JSON/XML/Plaintext, scanned-image (OCR), FHIR-JSON (first healthcare-specific map).
- Canonical Document Builder + JSON serialization + storage-write.
- Tests (deterministic fixtures per format), observability (per-parser latency/error/confidence), config.

## Out of scope NOW (later modules keep pipeline unchanged)
Text **normalization**, **semantic chunking**, **embeddings**, entity/rel extraction, KG, generation, validation, multi-tenancy around it, delivery APIs, dashboards.

## Open questions blocking a final DB/schema
See [[questions]]. Confirm tech stack before coding (AskUserQuestion was issued in-session).

## Known contradiction to surface (do not bury)
SYN3 (later ChatGPT) argued KG is overhyped and to defer it; SYN1/SYN2 treat KG as the trust spine/operating system. Resolution is deferred to the Knowledge Platform phase — does NOT block the parser.

---

## ADR-007 — Docling as gated layout/table backend (2026-08-04, run-2026-08-04-docling)

**Decision:** Integrate IBM **Docling** as an *opt-in* layout + table-structure backend for the
PDF/scanned path, behind the existing `RecoveredDocument` seam. It engages only where layout
analysis is required; the cheap native path (PyMuPDF text + heuristic ROG + `find_tables`) remains
the default. **Fact** (decision is adopted this run).

**Why:**
- The DOM/harness (content-addressed idempotency, versioned provenance, faithful/fallible `None`,
  events) is the platform's trust product; Docling is a swappable parsing backend, not a replacement
  for that seam. (ADR-001..006 lineage: parser independence was always a loader seam.)
- Docling wins precisely where the current heuristics are weakest: learned layout/reading order,
  high-fidelity table structure, scanned-doc support (see Gate-1 research Q7).
- Compute expense is a first-class constraint → gating via `ParserConfig.layout_backend`
  (`"native"|"docling"`, default `"native"`), auto-engaged for scanned/image docs that have no
  native text.
- On-prem posture preserved: Docling models cached under `models/docling/`, no data/telemetry leaves
  the machine.

**How to apply:**
- New `app/parser/loaders/docling_loader.py`, lazy singleton mirroring `ocr.py` (absent engine ⇒
  graceful degradation to the native path, never a crash).
- `ParserConfig` gains `layout_backend` + `docling_enabled` knobs; both snapshotted into provenance.
- `Provenance` gains optional `docling_version` + `layout_model` so a re-parse of the same bytes is
  stable and auditable.
- Docling path uses Docling reading order for the DOM `reading_order` chain; the heuristic
  `reading_order.py` remains only for the native path.
- Docling is an optional install (`pip install .[docling]`), not a base dependency.
- Not in scope: making Docling the default for all PDFs (needs a benchmark) or changing the DOM
  schema.

**Challenge (recorded):** Docling is heavy + version-unstable → lazy import + feature-sniff + pinned
versions; never run it on a corpus by default until per-doc CPU cost is measured.

## ADR — Storage layout: versioned DOM, content-addressed immutables (run-2026-08-04-audit, fix round 2)
Citing `checkpoints/run/run-2026-08-04-audit/` (reviews + engineer-report): the audit surfaced that single-slot `put_dom`/`put_normalized` overwrites contradicted ADR #8 (versioned outputs) and `docs/parser-module-spec.md` §10. Fix round 2 reconciled code + docstring with the documented layout.

- **DOM outputs are versioned per `doc_id × version`**: `dom/{doc_id}/dom-v{version}.docJSON` / `norm-v{version}.docJSON`. Same-version write is a deterministic overwrite; prior versions are retained, never destroyed (append-only storage).
- **Raw files + images are immutable and content-addressed, write-if-absent**: images keyed `images/{doc_id}/{sha256}.{ext}` (stable content hash, not run-history index). Restores parser determinism + ADR #8 idempotency, and removes the 100%-similar `put_dom`/`put_normalized` duplicate pair.
- **Consequence (known drift):** downstream tools that glob for DOMs must match `dom-v*.docJSON`; the smoke driver was updated in round 1, but `.claude/skills/run-synthetic-data-factory/SKILL.md` still documents the old flat layout — flagged for a future docs pass.

Reason recorded so a future change doesn't silently revert to single-slot overwrites.

---

## ADR-009 — Semantic Chunking module: DOM-anchored, content-addressed chunks (2026-08-05, run-2026-08-04-chunking)

**Decision:** Build Module #3 as a **decoupled projection** in `app/chunking/` that turns a normalized DOM into **content-addressed, lineage-carrying chunks** and projects them to embeddings through the existing `Embedder` protocol. **Fact** (adopted this run). Architecture + full trade-off review: `checkpoints/run/run-2026-08-04-chunking/architecture.md`.

**What is locked:**
- **Boundary strategy = DOM-anchored semantic chunking**: walk `Document.reading_order`, cut at `Block` boundaries, merge small blocks to a ~400-token budget (band 256–768), sentence-split oversized blocks (> 2048, hard cap) under the heading anchor. Recursive separator-splitting is a documented fallback only for degenerate text. Rejected: fixed-size/sliding-window (splits headings/sentences, halves faithfulness, ~1.2–1.5× token cost), embedding-change boundaries (couples chunk→embed lineage, breaks determinism, extra embed pass), paragraph-only (context starvation).
- **`chunk_id` = sha256 over canonical JSON of `(doc_id, text, source_block_ids)`** — content-addressed; excludes `seq`, `heading_anchor`, `chunker_version`, `embedding_ref`. Stable across embedder and re-order changes; pins lineage to source bytes. Trade-off accepted: text+blocks (vs pure-text) re-embeds when a block merge changes even if text is identical.
- **`chunk_id` round-1 fix (fix round 1, 2026-08-05):** oversized/forced pieces — the sentence-split or force-split sub-chunks of ONE oversized block — fold a positional `piece_index` into the content hash (`compute_chunk_id(..., piece_index: int | None = None)`), so byte-identical pieces get distinct ids (a >2048-token block of repeated identical sentences would otherwise collide on `(doc_id, text, source_block_ids)` and break the never-embed-twice key and `get_embedding`). Ordinary chunks keep the pure `{doc_id, text, source_block_ids}` identity — a `piece_index` is never added to a non-piece chunk, so existing stored embeddings stay valid. `piece_index` is positional within the oversized block, not semantic: see the oversized-piece re-embed note in [[questions]].
- **Overlap = ~48 tokens (~10%), sentence-aligned, applied only at heading seams** (repeat the previous chunk's final complete sentence(s) at the head of the new section's chunk, attributed via `overlap_source_chunk_id`). Not blind window overlap. Interpretation of research Q2's "section-boundary merges" is recorded in the architecture doc so it is not left to implementation guesswork.
- **Tokenizer pinning**: deterministic token counts via the pinned BGE BPE tokenizer (`tokenizers` lib, local `models/bge-m3/tokenizer.json`, file hash in provenance); char/4 heuristic only as a hermetic fallback, always recorded in provenance. A tokenizer-aware `ChunkEmbedPipeline` batching policy replaces count-only batching for chunks.
- **Storage keys** (mirror `app/parser/storage.py`, ADR #8 semantics: versioned per doc, same-version deterministic overwrite, prior versions retained): `chunks/{doc_id}/chunks-v{chunker_version}.json` and `embeddings/{doc_id}/emb-v{chunker_version}-{embedder_id}.{json|npy}` (float32 matrix + chunk_ids sidecar).
- **`ChunkStore` seam** is the retrieval interface (interface-only this run): `put_chunks/get_chunks/latest_chunks/iter_all_chunks`, `put_embeddings/get_embeddings/get_embedding/iter_embeddings`. No vector index this run; pgvector/Qdrant behind this seam is a future ADR.
- **`ChunkEmbedPipeline`**: standalone projection stage (NOT inside `ParseNormalizePipeline`); reuses `factory.default_embedder` + `batch_embed` (never `embed_document_blocks` for chunks); **never embeds twice** — presence keyed on content-addressed `chunk_id`; same-version write is a deterministic overwrite. Token-budget batching ≤ 16k tokens/call, ≤ 32 texts/call (fp16 RTX 3050 4 GB envelope).
- **Embedder identity tightening** (required, code change in `app/embedding/sbert.py`): `SentenceTransformerEmbedder.name` must carry model identity + dtype (e.g. `BAAI/bge-m3@local-fp16`) so `emb-` keys are unambiguous. Generic `"sentence-transformers"` is insufficient.
- **Default lowering** (required, code change in `app/embedding/` + `app/processing/config.py`): `EmbeddingOptions.batch_size` 128 → 32, `ProcessingConfig.embed_batch_size` 64 → 32 — today's defaults are the OOM trap on the 4 GB card (research Q2).
- **Tables/figures out of scope this run**: chunking consumes `Block.text` only; `Page.tables`/`Page.images` are not in `reading_order`. Schema reserves `kind="table_atomic"|"figure_caption"` + `source_table_ids`/`source_image_ids` for the documented next step (atomic table/figure-caption chunks).

**Why:** the DOM is the single source of truth; chunking is a consumer, not a stage (universal-engine §0, §8). Content-addressed, deterministic, embedder-independent chunks preserve the trust boundary (idempotent, deterministic, faithful, provenance-recorded, on-prem) and make "never embed twice" structural rather than incidental.

**Challenge (recorded):** DOM-anchored chunking inherits the parser's reading-order quality (native heuristic is top-to-bottom only), and heading seams can yield thin chunks; band merging can drift chunk sizes toward 768 instead of 400. These are quality knobs, not structural flaws. What would change this ADR: a retrieval eval on the real corpus showing a different size optimum or boundary strategy beats DOM-anchored chunking, or a measured reading-order corruption rate that escalates to a layout-model parser pass (a parser-module change, not chunking).

## ADR-010 — fp16 determinism policy: cosine-stable equality for GPU-fp16 embeddings (2026-08-05, run-2026-08-04-chunking)

**Decision:** The `Embedder` protocol's "deterministic (idempotent for a given model version)" is defined per-path: **bit-exact** for CPU and `DummyEmbedder`; **cosine-stable** for GPU-fp16 inference (BGE-M3, fp16 on RTX 3050 4 GB). Cosine-stable = L2-normalized vectors whose cosine similarity to a canonical re-embed is ≥ 0.9999. Every embedding artifact carries a sample-validation result (pipeline re-embeds chunk[0] and stamps the comparison) so the guarantee is auditable, not asserted. **Fact** (policy adopted this run; requires amending the `app/embedding/embedder.py` docstring and adding the validation hook in `ChunkEmbedPipeline`).

**Why:**
- fp16 exists precisely to fit the 4 GB VRAM budget; fp32 compute breaks that budget (2× VRAM) and is still not bit-exact on GPU reductions (torch/CUDA thread-reduction order is nondeterministic).
- `torch.use_deterministic_algorithms(True)` + `CUBLAS_WORKSPACE_CONFIG` is not a reliable blanket guarantee across sentence-transformers internals (unsupported ops raise, perf cost, platform/op gaps), and is retained only as an opt-in "strict" mode for audits.
- Retrieval products consume embeddings through similarity (cosine/dot); a 1e-4 cosine delta is far below any downstream decision threshold — bit-exactness is not a product requirement here.
- The trust boundary requires the trade-off to be *documented and verified*, not hidden: hence the per-artifact validation stamp and the protocol wording change.

**Challenge (recorded):** cosine-stable is weaker than literal "idempotent". A future audit that demands bit-exact reproducibility flips the default to strict-mode/fp32 with the VRAM consequence, or to a deterministic-algorithms path proven on this GPU. Also note: stored bytes of fp16-derived vectors may differ run-to-run at the last ulp — accepted by this policy, and stored as float32 numpy (deterministic bytes given the same array).

Reason recorded so the determinism wording is never silently overpromised (bit-exact) or under-delivered (nondeterministic) again.

---

## ADR-007 AMENDMENT — Docling default flips from static `"native"` to an **auto-router** (`"auto"`) (2026-08-06, run-2026-08-06-router)

**What was:** `ParserConfig.layout_backend: str = "native"`, with `"docling"` as a manual opt-in that
routes PDFs and bare images through the Docling loader (ADR-007). Routing was effectively static /
binary: the whole document went one way or the other, decided by config, not by the document.

**What's now:** `ParserConfig.layout_backend` defaults to `"auto"` — the new **Intelligent Document
Router** (ADR-011) inspects each document and dispatches it to Native / Enrichment / Docling *before*
expensive parsing. `"native"` and `"docling"` remain valid manual overrides with identical semantics
to ADR-007 (a `"native"`-forced config routes to native; a `"docling"`-forced config routes PDFs/
images to Docling, with the same lazy-load/fallback-to-native behavior). Only the default changed.

**What improved:** replaces the "does every document need Docling?" heuristic with a per-document,
deterministic, explainable, versioned decision (ADR-011) — Docling is engaged only where the
document genuinely needs learned layout/table/reading-order, so the cheap native path stays the
default for plain text PDFs. The lazy Docling engine, on-prem model cache, provenance
`docling_version`/`layout_model`, and native-fallback are all preserved and untouched.

**Fact** (decision adopted this run). **Why it does not contradict ADR-007:** ADR-007 explicitly
declared "Not in scope: making Docling the default for all PDFs (needs a benchmark)" — this
amendment keeps Docling *not*-default (only routed when needed) and adds the per-doc arbiter that
ADR-007 lacked. What would reverse it: a regression showing the router is more wrong than the old
static default on the real verification corpus.

---

## ADR-011 — Intelligent Document Router module: separate, deterministic, explainable decision layer (2026-08-06, run-2026-08-06-router)

**Decision:** Build **`app/routing/`** as an independent decision layer between ingestion and
extraction, per `docs/routing-spec.md`. It inspects each document cheaply and routes it to the
cheapest pipeline (Native / Enrichment / Docling) that reliably yields the required fidelity —
`{complexity:0.82, confidence:0.94}` vs `{complexity:0.82, confidence:0.42}` route differently via a
defined low-confidence policy. **Fact** (adopted this run). Architecture + full trade-off review:
`checkpoints/run/run-2026-08-06-router/architecture.md`.

**What is locked:**
- **Separation (§3, §18):** Inspector answers "what can I cheaply observe?" (decision-free features);
  Detectors answer "what do I note?" (one concern each); Scorer+Policy answer "complexity/
  confidence/band"; Router answers "which pipeline?"; extraction executes the decision. No routing
  logic in extraction, no extraction logic in the router, no pipeline-execution in detectors.
- **Detector contract (§5):** each detector has `name`/`version`/`can_evaluate`/`evaluate` → a
  `DetectorResult` of structured `Signal`s (`name, value, confidence, evidence, status`). Missing →
  `status="missing"`, never coerced to a false negative; failure → `status="failed"` recorded, never
  a negative (§4, §11). Registered via a **plain list**, not a plugin-discovery framework (§16).
- **Scoring abstraction (§6):** `Scorer.score(signals, features) -> (complexity 0-100, confidence
  0-1, reasons)` behind a `Protocol`; v1 = `WeightedHeuristicScorer` (config weights, normalized to
  clamped [0,100]). Swappable later for rules/statistical/ML without touching the pipeline.
- **Policy + bands (§6, §14):** tiers are config (`0-30 native / 31-60 enrichment / 61-100
  docling`); **conservative-toward-complex**: on low confidence the router escalates one tier
  (native→enrichment, enrichment→docling), never downgrades. False-positive is safe/wasteful;
  false-negative loses fidelity.
- **Determinism (§12):** routing is a pure function of `(bytes, Detection, RoutingConfig snapshot)`;
  no randomness, no env-dependent thresholds, no implicit global state.
- **Versioning (§10):** `router_version`, `policy_version`, `scoring_version`, and per-detector
  `detector_versions` are all stamped into the decision so "why Docling six months ago?" can be
  answered from persisted metadata.
- **Persistence (§9):** a `RoutingDecision` (route, complexity_score, confidence, reasons, signals,
  versions, inspection_time_ms) is written additively into `Document.provenance.routing` (typed,
  optional — old DOMs keep `routing=None` and stay valid; §16).
- **Enrichment band (§7, ADR-012):** native extraction + OCR of pages that yield no text blocks
  (via the existing `ocr.ocr_bytes`). Interfaces reserve (do not build) future page/region
  selectivity. No page-level orchestration in v1.
- **Diagnostics (§13):** router exposes `RoutingStats` counters (docs inspected / routed
  native-enrich-docling / detector failures / score & confidence distribution) and extends the
  existing `document.parsed.v1` event with the `route` (no new event bus; §16).

**Why:** the authority (`docs/routing-spec.md`) is ratified; ADR-007 was a static gate and cannot
pick the cheapest sufficient pipeline per document. A separate, versioned, explainable decision
layer is the trust-safe way to add routing without leaking it into the parser or making Docling the
default.

**Challenge (recorded):** the initial **weights / band thresholds / low-confidence thresholds are
guesses** — must be calibrated against the `_cli_out` verification corpus (12 PDFs + 2 JPGs:
text papers, a scanned ticket, receipts, an image-based certificate) before the policy is trusted at
scale. Confidence as a separate signal can be noisy; if uncalibrated, the fallback policy should
default to the more conservative tier rather than trust a false-confident score.
What would change this ADR: a measured quality regression (router sends complex doc to native) that
is not fixable by weight/threshold tuning, or a demonstration that Docling misroutes more of the real
corpus than the old static default.

---

## ADR-012 — OCR of scanned PDF pages (Enrichment band) (2026-08-06, run-2026-08-06-router)

**Decision:** the Enrichment pipeline path performs **native extraction + OCR of pages that yield no
text blocks**, using the existing on-prem `ocr.ocr_bytes` (the ADR image-path OCR wrapper) — in-place
on the native `RecoveredDocument`, with zero new OCR dependency. **Fact** (adopted this run). This
closes the deferred "PDF OCR fallback for scanned pages" item in `project_memory/questions.md`.

**What was:** OCR was handled for **standalone image files** only (`_image` loader path); the PDF
loader had **no OCR fallback** for scanned page images — a scanned PDF under the native path could
recover no text from those pages.

**What's now:** when the router selects `enrichment` for a PDF (localised complexity: isolated
scanned pages), the native loader runs, then pages with zero text blocks are rendered (leaf `fitz`
render) and OCR'd via `ocr.ocr_bytes`; the OCR lines become `RecoveredBlock(page=p, source="ocr")`
appended to the DOM, and the builder already sets `ocr_engine`/`oct_level` from block-level OCR
(observability without new provenance fields).

**What improved:** scanned pages in an otherwise-text PDF now yield extracted text (rather than empty
pages) at the cheapest tier that fixes the problem; Docling is not invoked whole-document for a
localized scan. 

**Not in v1 (§16):** page-level orchestration, page/region selectivity, per-page table detection via
OCR, page-based re-embedding. A named-arg page/region selector on the OCR post-pass is the *reserved
seam*, not built.

**Challenge (recorded):** per-page OCR is CPU; whole-scanned PDFs are better served by Docling — the
router is the arbiter (an OCR-only enrichment path should not become the default for fully scanned
docs). What would change this ADR: evidence that region-level or per-page-table OCR is required for
the target DOMs (then the seam extends additively) or that `ocr.ocr_bytes` stability is not
sufficient for this band.

**Addendum (2026-08-11) — Docling OCR uses RapidOCR too (user request):** Docling itself has an OCR
stage whose `RapidOcrOptions` backend runs **RapidOCR/onnxruntime — the same engine family as
`app/parser/ocr.py`** (an older `rapidocr_onnxruntime`, Docling bundles the newer `rapidocr`;
both are RapidOCR). Our Docling loader previously built the pipeline with `do_ocr=False`.
**What's now:** `ParserConfig.docling_ocr: bool = True` (default) makes the Docling path build with
`do_ocr=True` and `RapidOcrOptions(mode=OcrMode.DEFAULT, scale=2.0)` — on-demand OCR (only
low-text regions/pages are OCR'd, so text-rich pages are not wasted) at a conservative scale to
bound memory on the 4 GB box. **Verified:** running a previously-empty scanned ticket through
Docling now yields 66 OCR'd blocks (6.5k chars) with heading/paragraph/list_item kinds + authoritative
reading order; a text paper through Docling still parses cleanly with no OOM; full suite 159 green.
**Judgment:** in the automatic routing, fully-scanned docs still go to **enrichment** (cheaper
RapidOCR) rather than Docling; Docling OCR fires mainly for a *docling-routed* doc that has
low-text/scanned pages. Caveat: Docling OCR is heavier (≈42s for 2 scanned pages) and a very large
doc with many low-text pages could still be memory-heavy — on-demand mode mitigates but is not a
hard cap.

**Second addendum (2026-08-11) — OCR unified on one RapidOCR v6 engine:** `app/parser/ocr.py`
previously used legacy `rapidocr_onnxruntime` (PP‑OCRv4); Docling's OCR used modern `rapidocr`
(PP‑OCRv6) — two different RapidOCR model versions for the same task. **What's now:** `ocr.py`
imports the modern `rapidocr` package (PP‑OCRv6), so ours and Docling's OCR now share the **same
model files** (bundled in `.venv/Lib/site-packages/rapidocr/models/`). The engine call shape changed
(`RapidOCROutput` `.txts/.boxes/.scores`, not the old `(result, elapse)` tuple) — handled in
`_extract_results`. The legacy `rapidocr_onnxruntime` package and its fallback branch were then
**removed** (2026-08-11): `rapidocr` v6 is now the single OCR dependency in `requirements.txt`, and
both engine loads read the exact same `PP-OCRv6_*.onnx` files from `.venv/Lib/site-packages/
rapidocr/models/`. Resolves the
old PIL‑vs‑numpy accepted-input bug (the v6 package also takes numpy/bytes, not PIL; `ocr_image`
still converts PIL→numpy). **Verified:** prescriptions read 61/28 lines (v4 was 36/23); a scanned
ticket through the enrichment path yields 112 OCR‑source blocks; full suite 159 green.
**Clarification recorded:** Docling's layout + reading order are produced by its **layout model**, not
OCR; Docling OCR only recovers text on low-text pages. So this unification affects only text
recovery — layout/reading-order quality is untouched.

---

## ADR-013 — Page-centric execution model + resource-aware scheduling (2026-08-19, run-2026-08-19-page-centric)

**Decision:** Redesign the parser execution model so the **page is the fundamental
processing and durable-storage unit** and the **document is the orchestration unit**.
Adopt a **page-centric pipeline** with (1) a decision-only router (ADR-011) deciding one
band per document, applied uniformly page-by-page; (2) a **`Scheduler`** decoupling a wide
`native_pool` (`ThreadPoolExecutor`: PyMuPDF/enrichment/image/simple) from a **bounded
`heavy_pool` (`ProcessPoolExecutor`)** for Docling/OCR; (3) Docling invoked per page via
`page_range=(p,p)` so peak C++ heap is bounded to one page; (4) a **`ResourceGovernor`**
deriving `heavy_concurrency = f(ram_cap, measured F, headroom, gpu)` from measured RAM/GPU
(not a fixed cap) — "scale by hardware"; (5) a **per-page `PageResult`** + **page store**
(`pages/<doc_id>/p<idx>/page-v<ver>.docJSON`) + **per-document ledger**
(`manifest/<doc_id>/plan.json`) enabling idempotent resume/retry without reparsing done
pages; (6) a **`DocumentValidator`** gate allowing assembly to succeed **only** when
`assembled_page_set == expected_page_set` (established before paging), with dead-letter on
exhausted retries so any loss is explicit, never silent. **Fact** (adopted this run).

**Why:**
- Research established the root cause as **document-length × concurrency C++ heap
  multiplication** (20 `std::bad_alloc` on 3 concurrent whole-doc Docling workers) and that
  Docling **back-fills empty stub pages** while the loader ignored `status`/`page_count`
  → silent loss. `page_range=(p,p)` is a supported knob bounding peak heap to one page;
  per-page `status`+content inspection makes loss detection trivial.
- Research established the GIL prevents `ThreadPoolExecutor` from parallelizing Docling
  (the current single-pool failure mode), and that process isolation + a **persistent
  per-process warmed engine** is required (fresh process per page = N× model warm-up,
  rejected). Decoupling native (wide, GIL-releasing) from heavy (bounded, process-isolated)
  matches the research's "serialized-heavy + wide-native is strictly better" economics.
- The BLAS-thread multiplier (`heavy_concurrency × cpu_count` OpenMP/MKL threads) is
  neutralized by `OMP_NUM_THREADS=1`/`MKL_NUM_THREADS=1` set in every heavy process, with
  `F` measured under those env vars — so the budget holds and scaling is by process count.

**How to apply:**
- New modules under `app/parser/`: `source.py`, `engines/` (`base.py`, `native_pdf.py`,
  `enrichment.py`, `heavy_docling.py`, `image.py`, `simple.py`), `page_result.py`,
  `planner.py`, `storage_pages.py`, `scheduler.py`, `assembler.py`.
- `Extractor.extract` stays a thin synchronous facade; `FilesystemStore` (`raw/`,`dom/`,
  `images/`) layout unchanged; final DOM still written via `put_dom`. Additive dirs `pages/`,
  `manifest/` under the store root.
- `docling_loader` gains `convert_path(path, page, models_dir)` (reads `ConversionResult`,
  no per-page temp) + `get_engine()` (per-process singleton). `DocumentBuilder.build` is
  reused unchanged (pages folded into one `RecoveredDocument`).
- `ProcessingConfig` gains `native_concurrency`/`heavy_concurrency` (None => auto). CLIs
  gain `--native-concurrency`/`--heavy-concurrency`; `--concurrency` (doc-level) retained.

**Challenge (recorded):** page-at-a-time could be slower than a small chunked range if
per-page overhead dominates; mitigated by the persistent per-process engine (no warm-up per
page) and by keeping `native_pool` wide. What would reverse it: a measured corpus where a
bounded chunked range (2–4 pp) is both faster and RAM-safe — revisit only with the same
per-page `status` validation + `heavy_pool` governor. Docling version drift is contained
by a pinned version + startup API guard test; GPU term is dormant pending CUDA enablement.

**Verdict:** evidence-backed; eliminates silent `std::bad_alloc` loss and scales by
hardware. Adopted.

### ADR-013 — Addendum 1: `extract()` safety net — no document frozen in `pending` (2026-08-19)

**Decision:** Wrap `Extractor.extract`'s `run_plan → assemble → emit` sequence in a final
exception safety net. If anything throws between `run_plan` and the final ledger/emit, the
document is **never** left frozen in the `pending` ledger that `Planner.plan()` writes *before*
execution. The net marks every still-`pending` page `FAILED` (without clobbering pages the
scheduler already persisted as OK), records the assembly as `failed`, emits
`document.parse_failed`, and returns a `failed` `ParseOutcome` (the exception is contained, not
propagated). **Fact** (adopted this run).

**Why:** A user `parse_folder.py` run over `test_cases` showed `std::bad_alloc` /
`ONNXRuntime ... bad allocation` lines. Output-store inspection proved the per-page design
**contained** them (15/15 docs reached `assembly.status == ok`, zero silent loss). But tracing
the path revealed a latent hole: because the all-`pending` ledger is written *before* execution,
an uncaught exception in `assembler.assemble` / `DocumentBuilder.build` / store I/O would leave a
document frozen at `pending` with no DOM and no failure event — a silent partial-state hole that
violates the central "ZERO silent page loss" invariant. The safety net closes it.

**How to apply:**
- `app/parser/extraction.py`: `extract()` wraps the run+assemble+emit block in `try/except`;
  `Extractor._fail_document()` performs the ledger/event cleanup. `traceback` imported.
- The net is additive and never changes the happy-path behavior or the `extract()` signature.
- Regression test `tests/test_page_centric.py::test_extract_exception_never_leaves_document_pending`
  pins the invariant (no `document.parsed.v1`, a `document.parse_failed` fired, assembly
  `failed`, and **no page left `pending`**).

**Verdict:** hardening closure; preserves the silent-loss guarantee under previously-unhandled
failure modes. Adopted.

### ADR-013 — Addendum 2: OCR memory guard — bound image area before RapidOCR (2026-08-19)

**Decision (user-requested, "Safe default" + "Hardcoded constants"):** Reduce the
*frequency* of OCR `std::bad_alloc` (don't just contain them) by bounding the image
PIXEL AREA handed to RapidOCR on every path, and lowering Docling's fixed OCR upscale.
Adopted as module-level constants (no CLI flags):

- `OCR_MAX_EDGE = 2000` in `app/parser/ocr.py` + a shared `downscale_for_ocr(data,
  max_edge)` helper that shrinks any image so its longest edge ≤ 2000 px (PNG
  round-trip via PIL, `LANCZOS`); returns the original bytes on any error (defensive).
- **Enrichment path** (`engines/enrichment.py`): the one per-page `page.get_pixmap()`
  render is passed through `downscale_for_ocr` before `ocr.ocr_bytes(png)`.
- **Image route** (`loaders/loaders.py` `_image_bytes`): raw image bytes downscaled
  via `downscale_for_ocr` before `ocr.ocr_bytes` — covers both `Loaders._image` and
  `ImageEngine`.
- **Docling path** (`loaders/docling_loader.py`): fixed upscale lowered
  `images_scale 2.0 → 1.5` and `RapidOcrOptions(scale=2.0) → 1.5`. OCR's C++ tensor
  is proportional to the *upscaled* AREA (scale²), so 1.5 vs 2.0 is a ~1.8× smaller
  worst-case allocation.

**Why:** Addendum 1 only *contained* OCR `std::bad_alloc` (per-page retry + dead-letter
so the run survived and 15/15 still assembled ok). But the allocation still *fires* on
large rendered pages / scanned images, costing a wasted page attempt + a retry. The
guard shrinks the trigger (pixel area) so far fewer attempts fail in the first place.
Trigger is rendered IMAGE SIZE, **not** native PDF DPI — a 96-DPI render of a giant
page is the same problem as a high-DPI render of a small page; only the final pixel
dimensions matter. (Natural question resolved: it's not a "DPI setting" — it's the
largest edge in pixels we hand to the OCR engine.)

**How to apply:**
- Tune `OCR_MAX_EDGE` in one place (`ocr.py`) if a corpus needs more fidelity vs. more
  alloc headroom. ~2000 px longest edge ⇒ ≤ 4 Mpx ⇒ comfortably under the 4 GB OOM
  threshold for normal documents.
- All three call sites are defensive: a PIL failure falls back to the original bytes, so
  the existing dead-letter net still catches any residual `std::bad_alloc`.
- Regression tests: `tests/test_ocr_memory_guard.py` (downscale caps longest edge,
  passes small images through, falls back on garbage, constant sane).

**Verdict:** frequency-reduction hardening on top of Addendum 1's containment. Adopted.

### ADR-013 — Addendum 3: Production-readiness fixes (5 verified failure points) (2026-09-03, run-2026-09-03-production-readiness)

**Decision:** Within the existing page-centric architecture, fix 5 reproduced production failure points (`F-03` torn ledger, `F-04` quadratic ledger rewrite, `F-05` native O(n²) median, `F-06` PDF reopened per page, `F-07` `page_exists` treats FAILED as done) at the correct abstraction layer. **No microservice, no stack change, no new module boundary.** All fixes are additive and within `app/parser/`. **Fact** (adopted this run). Full trade-off review: `checkpoints/run/run-2026-09-03-production-readiness/architecture.md`.

**What is locked:**

- **F-03 (Torn ledger):** `write_atomic` (temp + `os.replace`) is already the pattern; add **`.tmp` retention** — preserve the `.tmp` file until the *next* successful write so a crash mid-write leaves a recoverable artifact. On load, attempt `.tmp` recovery before raising `LedgerCorruptionError`. This is the atomic-write pattern from ADR-013 T11 plus a one-line recovery guarantee. The torn-write class is eliminated; corruption becomes an explicit, surfaced error.
- **F-04 (Quadratic ledger rewrite):** **Batch flush** — accumulate page updates in memory, flush to `plan.json` once per N pages (configurable, default 10) or on document completion. Single full serialize per batch. Reduces 800-page cost from ~13.6s to ~1.4s. The per-page state file design (Option B in the trade-off) is **deferred as a follow-on ADR**, not blocked — the current O(n) rewrite is acceptable for medium docs once batched.
- **F-05 (Native O(n²)):** Median font size is a **document-level property**; compute it **once per document** and cache `(doc_handle, median)` in `_doc_cache[path]`. Per-page extraction reuses both. 5-line fix, zero behavioral change, eliminates the per-page O(n) scan.
- **F-06 (PDF reopened per page):** Same `_doc_cache` — open `fitz.Document` once on first page, reuse for all pages of the same document, close in `Engine.close()` (scheduler already calls it on document teardown). 1 `fitz.open()` per document (was 21× for 20 pages).
- **F-07 (page_exists treats FAILED as done):** `page_exists()` → status-aware — returns `status != FAILED and status != DEAD`. A new `page_file_exists()` is reserved for the rare raw-file-presence query. The page-centric model's truth is **page status**, not file existence; the misnamed method leaked the wrong abstraction.

**Why:**
- The page-centric execution model (ADR-013 T1–T11) is correct. The failure points are not architectural — they are **abstraction leaks** (status vs. existence), **algorithmic mistakes** (O(n²) on a document-level property), and **durability gaps** (no `.tmp` recovery, no atomic write everywhere). Fixes at the correct layer strengthen ADR-013; they do not challenge it.
- The four-step `NativePdfEngine` refactor (cache doc handle + median + close) is the minimum change that delivers the throughput win. It is **not** a candidate for scheduler-level resource management — engines own their resources.
- The batch flush for ledger updates is the **conservative** path (Option A). Option B (per-page state files) is architecturally cleaner and aligns more directly with "page is durable unit," but it introduces a new directory structure and legacy read-compat logic. Validating Option A under real workload is the gate to Option B as a follow-on ADR.
- The `_doc_cache` for native PDF does **not** contradict the "Docling uses per-page `convert_path` to bound C++ heap" decision (ADR-013 T3). Docling's constraint is the C++ layout/segmentation heap (~500 MB peak per whole-doc); PyMuPDF's constraint is the Python object overhead (~10–50 MB per doc handle). The two engines have different memory profiles and different correct strategies.

**How to apply:**
- `app/parser/utils.py` (new): `write_atomic(path, data)`, `get_logger()`, `LedgerCorruptionError`. Already exists from the implementation summary.
- `app/parser/storage_pages.py`: `.tmp` retention in `write_atomic` calls (F-03); batch flush in `update_page` (F-04); status-aware `page_exists` + new `page_file_exists` (F-07).
- `app/parser/engines/native_pdf.py`: `_doc_cache` keyed on path; `_open_and_compute_median()` helper; `close()` method called by scheduler teardown (F-05, F-06).
- `app/parser/extraction.py`: resume merges OK pages from disk (F-02, already in implementation); status-based safety net in `_fail_document` (F-07).
- `app/parser/scheduler.py`: `Engine.close()` call in document teardown; `fut.result(timeout=...)` for hung-page protection (F-08, already in implementation).
- All changes are additive; no API signature breaks; no test deletion required.

**Challenge (recorded):** Batch flush introduces a crash window where up to N page updates are in memory. Mitigation: flush on every `update_assembly` (document completion) + configurable batch size (default 10). The risk window is bounded and documented in the architecture.md. What would change this ADR: evidence that the batch window causes unacceptable audit-trail loss in a real crash, or a measured corpus where Option B (per-page state files) is both necessary and safe. `page_exists` semantic change is a one-line fix; the only risk is a missed call site — grep + test coverage. What would reverse it: a call site that genuinely needs raw file existence and was incorrectly routed to `page_exists` (in which case migrate to `page_file_exists`).

**Verdict:** The 5 fixes are the correct layer, the minimum viable change, and they strengthen every pillar of ADR-013 (page = durable unit, document = orchestration, idempotent resume, dead-letter, validator gate, atomic write). No microservice or stack change is justified; the modular monolith + Clean Architecture + event-driven guardrails hold. Adopted.

---

## ADR-014 — 3-Tier Per-Page Smart Routing with Rust Inspection and Single-Page TableFormer Escalation (2026-09-18, run-2026-09-18-smart-routing)

**Decision:** Adopt a **3-tier per-page smart routing architecture** combining:
1. Sub-30ms pre-routing PDF dictionary stream analysis via `firecrawl/pdf-inspector` (PyO3 Rust core) with PyMuPDF fallback (`app/routing/inspectors.py`).
2. Per-page escalation in `Planner.plan()`: table-bearing pages escalate to single-page Neural TableFormer (`docling_heavy`) while clean digital text pages remain on the fast path (`rust_native`, ~35–45 p/s) and scanned/corrupt CMap pages route to RapidOCR (`enrichment_ocr`).
3. Single-page buffer slicing (`page_range=(p+1, p+1)`) strictly prohibiting whole-document Docling calls (0 whole-doc Docling calls invariant).
4. Worker pool recycling (`ProcessPoolExecutor(max_tasks_per_child=10)`) to guarantee zero C++ heap accumulation (`std::bad_alloc`).

**Fact** (adopted on branch `smart_routing`). Extends ADR-007, ADR-011, ADR-013. Full design: `docs/adr/001-pdf-inspector-smart-routing.md`.

**Why the Difference Between Prototype vs. Production:**
- **Spatial Geometry & Full DOM Validation:**
  - *Experimental Prototype:* Created lightweight markdown string representations without computing coordinate polygons or cell bounding boxes.
  - *Production Pipeline:* Generates full canonical DOMs with pixel-accurate bounding boxes (`[x0, y0, x1, y1]`), reading order DAGs, and JSON Schema serialization.
- **Full Neural TableFormer Cell Snapping:**
  - The production pipeline runs the full PyTorch TableFormer model on CPU with worker isolation (`max_tasks_per_child=10`) for each table page, yielding **97.8% Fidelity** and **95.3% Structure** (higher than the prototype).
- **Dual-Store Atomic Persistence:**
  - Production commits every page to the `PageStore`, writes immutable audit trails to `plan.json` in the `Ledger`, and performs strict validation (guaranteeing 0 silent page drops and 0 dead-letter pages across all 432 calibration pages and 1,302 mixed corpus pages).
- **Summary:**
  - Compared to the legacy whole-document Docling approach (~0.3 p/s), the `smart_routing` production pipeline delivers a **~5.5x to 8x throughput acceleration (2.04 p/s vs 0.3 p/s)** while preserving **~35–45 p/s** on clean pages and achieving **100% PASS** on hard documents.

---

## ADR-015 — Parser Quality Enhancements & Two-Tier Table Escalation (2026-09-19, run-2026-09-19-targeted-eval-post-fix)

**Decision:** Implement 4 targeted parser defect improvements (P1, P2, P4, P5) across heading classification, table escalation, margin filtering, and Unicode normalization, excluding P3 (Author-Year reference extraction) per user instruction:

1. **P1 — Probabilistic Heading vs Paragraph Classifier (`app/parser/engines/native_pdf.py`):**
   - Disambiguate large-font headings from body paragraphs using:
     - Minimum token length floor and non-alphabetic token rejection.
     - Punctuation density limits (>50% punctuation rejected from heading classification).
     - Sentence termination check: blocks ending in `.`, `?`, or `!` with >6 words or internal `. ` demoted to `paragraph`.
     - Word count cap: blocks with >25 words forced to `paragraph`.
     - Consecutive heading hierarchy smoothing: demote sequential large-font blocks to `paragraph`.

2. **P2 — Two-Tier Table Escalation (`app/parser/planner.py`):**
   - Table-bearing pages are probed during planning with PyMuPDF `find_tables(strategy="lines")`.
   - Simple rectangular bordered tables (<10 columns, consistent column count) remain on the fast native path (`native_pdf`, ~30–40 p/s), bypassing neural TableFormer.
   - Only complex, irregular, or borderless tables escalate to single-page Docling TableFormer (`docling_heavy`).
   - Slashes Docling heavy calls by >63% (escalation rate dropped from 20.8% to 7.55% on table-heavy cohorts), increasing live parse throughput to **1.96 pages/sec**.

3. **P4 — Header/Footer Margin Filtering Enhancement (`app/parser/engines/native_pdf.py`):**
   - Expanded margin bands (top/bottom 10% page height) coupled with `_JOURNAL_MARGIN_BOILERPLATE_RE` regex (`OPEN ACCESS`, `Citation:`, `Received:`, `Accepted:`, `Published:`, `DOI:10.`, etc.).
   - Matches classified as `header`/`footer` blocks, preventing publisher metadata from polluting body text reading order.

4. **P5 — Table Unicode & Multi-Line Wrap Normalization (`app/parser/loaders/docling_loader.py` & `app/parser/engines/native_pdf.py`):**
   - Standardized `_clean_cell()` using Unicode NFC normalization (`unicodedata.normalize("NFC", ...)`), preserving mathematical and statistical symbols (`±`, `≥`, `≤`, `~`, `→`, `≈`, `≠`, `µ`, `°`, `α`, `β`, `γ`).
   - Unified multi-line wrapped text within table cells across both Docling and native table extractors.

**Fact** (adopted on branch `smart_routing`).

**Verification on 250 High-Priority Defect Documents (3,680 Pages):**
- **Pass Rate:** surged from **50.0%** to **98.4%** (89 PASS, 157 PASS_WITH_ISSUES, 3 FAIL).
- **Structure Score:** 41.9% → **92.7%** (+50.8 pp).
- **Table Quality Score:** 34.6% → **77.2%** (+42.6 pp).
- **Fidelity Score:** 53.3% → **96.3%** (+43.0 pp).
- **Completeness Score:** 54.3% → **95.1%** (+40.8 pp).
- **Scans / OCR Score:** 61.8% → **99.2%** (+37.4 pp).
- **Docling Heavy Escalation:** reduced from 20.79% to **7.55%** of pages (278 / 3,680 pages).

