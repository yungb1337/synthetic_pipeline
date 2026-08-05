# Research — run-2026-08-04-chunking (Module #3 Semantic Chunking)

**Recorded by:** Project Orchestrator, from the Research Lead's report (research-lead is read-only).
**Evidence base:** local sources only (inference gateway rate-limited; one web attempt returned empty — noted, no fabrication).

**RESEARCH: COMPLETE**

## Q1 — Chunking strategy (Recommendation)
**Structure-aware (DOM-anchored) semantic chunking.** Walk `Document.reading_order`, cut at `Block` boundaries (heading/paragraph/table/list item), merge small blocks up to a token budget, split oversized blocks at sentence boundaries preserving the heading anchor.
- Rejected as primaries: fixed-size/sliding-window (cuts mid-sentence, halves retrieval faithfulness, ~1.2-1.5× token cost), embedding-change-boundary (decides boundaries with the embedder → couples chunk→embed lineage, breaks determinism, extra pass).
- Recursive separator-splitting kept only as documented fallback for degenerate text (e.g., one unbroken cell).
- Parent-child/context-injection reserved in schema (`parent_id`, `heading_anchor`) but not built this run (retrieval is interface-only).
- Matches already-adopted direction in `docs/universal-document-understanding-engine.md` §8.

## Q2 — Size / overlap / batching (Recommendation)
- Target **~400 tokens** (band 256-768); **hard cap 2048** (well under BGE-M3's 8192 ceiling → no silent truncation). BGE-M3 config (local `models/bge-m3/config.json`): xlm-roberta, hidden 1024, 24 layers, max_pos 8194.
- Overlap **~48 tokens (~10%)**, sentence-aligned, only at section-boundary merges — not blind window overlap.
- fp16 on RTX 3050 4GB: B≈16 at L=512 fits; B≈32 at L=1024 near OOM. **Token-budget batching (≤ ~16k tokens/call, count cap ≤32) is the robust policy.**
- **Current defaults are the OOM trap:** `EmbeddingOptions.batch_size=128` (factory), `batch_embed` default64, `ProcessingConfig.embed_batch_size=64` → align downward.

## Q3 — Chunk metadata / lineage (Recommendation)
Per-chunk: `chunk_id` (content-addressed sha256), `doc_id`, `seq`, `kind`, `text` (faithful Block.text join; None never fabricated), `source_block_ids`, `page`, `heading_anchor`, `token_count`/`char_count`, `provenance{chunker_version, params, dom_schema_version, normalizer_version}`, `embedding_ref`.
Storage keys (mirror `app/parser/storage.py`): `chunks/{doc_id}/chunks-v{chunker_version}.json` · `embeddings/{doc_id}/emb-v{chunker_version}-{embedder_model@rev}.{json|npy}`.
Retrieval seam (interface-only): `ChunkStore` mirroring `Store` — `put_chunks/get_chunks(doc_id, version)`, `iter_all`, `get_embedding(chunk_id)`, `iter_embeddings`.

## Q4 — Embedding workflow (Recommendation)
- New projection stage `ChunkEmbedPipeline` (parallel to parse→normalize), NOT a parser stage. Reuse `factory.default_embedder` + `batch_embed`; never `embed_document_blocks` for chunks.
- **Never embed twice:** check existing `emb-v{ver}` for present chunk_ids, embed only missing; same-version write = deterministic overwrite.
- Artifact records embedder model/revision/dim/dtype/chunker version/token budget. Tighten `Embedder.name` to carry model identity (currently generic `"sentence-transformers"`).
- Hermetic tests use `DummyEmbedder`; real-BGE test gated on model availability. Fix `test_sbert_embedder.py` bge-small→bge-m3 in this run's test pass (tracked issue).

## Open risks / decisions for the architect
1. **fp16 GPU bit-level nondeterminism** vs the Embedder Protocol's strict determinism wording → pick policy (cosine-stable vs fp32 vs `torch.use_deterministic_algorithms`).
2. **Tables/figures live outside `Block.text`** (`Page.tables`, `Page.images`) — this run chunks Blocks only; atomic table/figure-caption chunks = documented next step.
3. **Tokenizer pinning** for deterministic token counts (pinned BGE tokenizer vs 4-char heuristic) — record in provenance.
4. **VRAM batch figures are arithmetic estimates (Inference)** — validate on the box.
