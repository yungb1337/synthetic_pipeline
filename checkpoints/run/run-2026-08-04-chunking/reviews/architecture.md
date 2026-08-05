# Architecture Review — run-2026-08-04-chunking

**Recorded by:** Project Orchestrator, from the Architecture Reviewer's report.

## Verdict
**VERDICT: PASS** (1 major + 6 minors to route back)

## Conformance (green)
- DOM-anchored chunking: band merge rule (`chunker.py:158-164`), `_resolve_order` walking reading_order with page fallback + orphan append (`:182-227`), sentence-split oversized under heading anchor + forced-split recorded (`:114-137,230-281`).
- Overlap only at heading seams, sentence-aligned, attributed via `overlap_source_chunk_id` (`:138-151,325-327`) — matches architecture §3.3.
- Content-addressed `chunk_id` over canonical JSON `(doc_id, text, source_block_ids)` (`schema.py:67-79`), verified by `test_chunk_id_stable`.
- Versioned keys mirror parser storage; same-version deterministic overwrite, prior versions retained.
- `ChunkStore` retrieval seam, interface-only, 8 methods, no vector index.
- `ChunkEmbedPipeline` standalone, never-embed-twice keyed on chunk_id, token-budget batching ≤16k/32, event `chunk_embedded.v1`.
- ADR-010 cosine-stable stamp (`pipeline.py:185-189`); Embedder identity tightened (`sbert.py:45-70`); batch defaults lowered (factory/processing →32).
- Modular boundaries clean; chunking imports only dom/events/embedding; no circular imports.
- Future-proofing: reserved fields inert, nothing deferred is locked.

## MAJOR (route back)
- `app/chunking/chunker.py:230-257` + `schema.py:67-79` — oversized-block sentence pieces can be byte-identical (one >2048-token block of repeated identical sentences) → duplicate `chunk_id` within one artifact, breaking the uniqueness never-embed-twice (`pipeline.py:161`) and `get_embedding` (`store.py:178`) key on. Fix: discriminate oversized/forced pieces in identity (piece index/sentence span) OR assert chunk_id uniqueness at artifact build.

## MINOR
- `pipeline.py:61` — eager `default_embedder()` loads BGE-M3 into VRAM even for `chunk_only()`/CLI without `--embed`.
- `store.py:57-63` — `_version_key` mixes int/str tuples; `TypeError` if versions ever mix numeric + non-numeric (latent; all current versions numeric).
- `chunker.py:114-137` — oversized heading is sentence-split with the *previous* anchor and never becomes its own section anchor (pathological, deterministic).
- `chunker.py:143-151,156-164` — overlap-prefix tokens excluded from merge-budget count; `token_count` can run ~overlap over target (bounded).
- `pipeline.py:198-199` — sidecar `meta["normalize"]: True` asserted even for `DummyEmbedder` (does not L2-normalize) — cosmetic.
- `sbert.py:76` — constructor still defaults `batch_size=128` (only factory/processing lowered).
- `factory.py:9-11` — docstring says model "downloads to local HF cache on first use", at odds with on-prem/no-download posture.
