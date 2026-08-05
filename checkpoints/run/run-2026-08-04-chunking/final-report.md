# Final Report — run-2026-08-04-chunking

**Objective:** Module #3 — Semantic Chunking + wiring the real BGE-M3 embedder into the chunk→embed pipeline, building the retrieval-grounding seam.

**Result:** ✅ Both reviewers **PASS** · pytest **99 passed / 1 skipped** (baseline 34) · duplication signal clean · real BGE-M3 verified on-box.

## What each gate produced
- **Gate 1 Research** → `research.md`: structure-aware (DOM-anchored) chunking recommended; ~400-token target / 2048 hard cap / 48-token heading-seam overlap; content-addressed chunk_id; token-budget batching for fp16 on the 4GB RTX 3050.
- **Gate 2 Architecture** → `architecture.md` `ARCHITECTURE: APPROVED` + **ADR-009** (DOM-anchored content-addressed chunks) + **ADR-010** (cosine-stable fp16 determinism policy).
- **Gate 3 Plan** → `implementation-plan.md` `PLAN: READY` (16 tasks, 3 tracks).
- **Gate 4 Implement** → new `app/chunking/` (config, schema, tokenize, sentences, chunker, batching, store, pipeline, cli) + embedding follow-ups. Suite 34 → 94.
- **Gate 5+6 Review** → both PASS with routed majors → **fix round 1** (chunk_id collision via `piece_index` discriminator; `_version_suffix` dedup; sbert batch default 128→32; lazy embedder; schema shadow; decimal-guard; on-prem docstring) → re-review **both PASS, zero remaining**. Suite 94 → 99.
- **Gate 7 Checkpoint** → `checkpoint.md`; `module_status.md`, `architecture_decisions.md` (ADR-009 piece_index clause), `questions.md` (4 tracked follow-ups) updated; `CLAUDE.md` layout now includes `app/chunking/`.

## The module
- **SemanticChunker** — walks `Document.reading_order`, cuts at Block boundaries, merges small blocks to ~400 tokens (band 256–768), sentence-splits oversized blocks (>2048) under a heading anchor, overlap (~48 tokens) only at heading seams with attribution. Content-addressed `chunk_id` = sha256 over canonical `{doc_id, text, source_block_ids}` (+ `piece_index` for forced pieces) → idempotent, lineage-carrying.
- **ChunkStore** — retrieval seam (interface-only this run), versioned keys `chunks/{doc_id}/chunks-v{ver}.json`, `embeddings/{doc_id}/emb-v{chunker}-{embedder}.{json|npy}`.
- **ChunkEmbedPipeline** — never-embed-twice (keyed on chunk_id), token-budget batching (≤16k tokens / ≤32 texts) sized for fp16 4GB, ADR-010 cosine-stable stamp.
- **Embedder** — BGE-M3 now the product embedder with identity-bearing `name` (`BAAI/bge-m3@26159e7a-fp16`); batch defaults lowered to 32.

## Real-path smoke (verified on the box)
`python -m app.chunking.cli --doc cli-smoke --store <tmp> --embed` → `chunks=2 embedded=2 skipped=0 dim=1024 dtype=float32 embedder=BAAI/bge-m3@26159e7a-fp16`.

## Where the project stands
Parser (Docling-gated layout), normalizer, batch/scale, GPU embeddings, and now **semantic chunks + chunk embeddings** are complete — the retrieval-grounding seam is in place. **Next: Module #4 — retrieval** (the "Unknown → trusted-source retrieval → evidence" loop), then ontology/KG, then the validation framework.

## Process notes
- This was the org's first **full forward run** (research → architecture → plan → engineer → reviews → checkpoint) — every gate fired in order.
- The review loop earned its keep again: it caught the chunk_id-collision edge case and the half-finished batch-default change before they'd bite.
- Web research was unavailable (gateway rate-limited) all run — the research gate ran on local sources with honest labeling, and the result was still architecturally solid.
