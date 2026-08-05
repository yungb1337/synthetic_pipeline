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