# Quality & Performance Review — run-2026-08-04-chunking

**Recorded by:** Project Orchestrator, from the Quality & Performance Reviewer's report.

## Verdict
**VERDICT: PASS** (2 majors + 7 minors to route back)

## Verification
- Suite: **94 passed, 1 skipped** (docling fallback skip, pre-existing). 3 warnings (1 new: `ChunksArtifact.schema` shadow).
- Similarity: 2 pairs. **`app/chunking/store.py:49` vs `app/parser/storage.py:101` `_version_suffix` — Jaccard 1.00 (identical body)**. `ocr.py:20` vs `docling_loader.py:52` `engine_available` — pre-existing, out of scope.
- Eager-`.get` fix verified CORRECT (`pipeline.py:178-182`); `test_never_embeds_twice` covers it.
- `compute_chunk_id` deterministic across runs/machines (canonical JSON, str + list[str] only).
- Batching policy correct (`batching.py:34`); real-BGE test gated + passes on CUDA box (cosine ≥0.9999, dim1024, float32).
- Test quality high: hermetic (char4 + DummyEmbedder), covers determinism/idempotency, boundary rules, oversized split, overlap attribution, never-embed-twice, batch caps.

## MAJOR
- `app/chunking/store.py:49` vs `app/parser/storage.py:101` — `_version_suffix` byte-identical duplication (Jaccard 1.00). No shared home today; recommend extracting a shared helper or importing the pure function from `app.parser.storage` (chunking already depends on `app.parser.dom`; not an internals leak).
- `app/embedding/sbert.py:76` — `SentenceTransformerEmbedder.__init__(batch_size=128)` still advertises the OOM-risky default A3 set out to retire; direct construction (not via factory) hits 128. Align to 32.

## MINOR
- `pipeline.py:142,213` — chunks artifact written twice per run (crash-safety tradeoff; write amplification at scale).
- `pipeline.py:61` — eager embedder construction loads BGE-M3 into VRAM even for chunk-only runs.
- `pipeline.py:187` — per-run validation re-embeds chunks[0] (one extra GPU call/doc); GPU-fp16 sidecar json not byte-stable across runs (cosine-stable, not bit-exact — consistent with ADR-010; document per-path).
- `schema.py:56` — `ChunksArtifact.schema` shadows BaseModel.schema → pydantic v2 warning.
- `sentences.py:79` — decimal false-splits (e.g. "BP120/80." or "Version2.0." followed by capital) — quality knob in numeric-heavy medical text, not a trust violation.
- `chunker.py:46,169-175` — `blocks_seen`/`order_source_used` report edge semantics (cosmetic).
- `store.py:170-181` — `get_embedding` full-matrix load per row (acknowledged seam; future vector store replaces it).
