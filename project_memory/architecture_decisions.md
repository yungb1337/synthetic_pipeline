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