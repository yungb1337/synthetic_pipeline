# Engineer Report — run-2026-08-04-chunking (Module #3: Semantic Chunking + BGE-M3 wiring)

**Engineer:** Implementation Engineer · **Date:** 2026-08-05 · **Plan status:** `PLAN: READY`
**Claim labels:** Fact | Research | Inference | Recommendation — applied throughout.

---

## 1. What was built (per track)

### Track A — Embedding follow-ups (all green)

- **A1** `app/embedding/sbert.py`: removed the generic class attr `name = "sentence-transformers"`; added `name` + `revision` properties. `name = f"{model_id}@{revision}-{dtype}"` where `model_id` is the HF identifier (never the filesystem path), `revision = sha256(config.json bytes)[:8]` when the local model dir exists else `"local"` (orchestrator decision), `dtype = fp16|fp32`. `app/embedding/embedder.py`: `Embedder` protocol docstring amended per ADR-010 (determinism is per-path: bit-exact CPU/Dummy, cosine-stable ≥ 0.9999 GPU-fp16; `name` must carry model identity + revision + dtype). `DummyEmbedder.name` unchanged.
- **A2** `tests/test_sbert_embedder.py`: fixture `BAAI/bge-small-en-v1.5` → `BAAI/bge-m3`; `_available()` also requires `models/bge-m3/config.json` (local, not a download); added `test_name_identity`.
- **A3** `app/embedding/factory.py` `EmbeddingOptions.batch_size` 128 → 32; `app/processing/config.py` `ProcessingConfig.embed_batch_size` 64 → 32; comments explain the 4 GB fp16 envelope rationale. `runner.batch_embed` default (64) left untouched per the plan (chunk path always passes an explicit batch size).

### Track B — new `app/chunking/` module (all tasks, B0–B10)

- `config.py` — `ChunkingConfig` (frozen dataclass, `snapshot()`), defaults exactly per the architecture table (400/256–768/2048/48, 16k-token & 32-text caps).
- `schema.py` — `Chunk`, `ChunkProvenance`, `ChunksArtifact` (pydantic v2), reserved fields (`parent_chunk_id`, `source_table_ids`, `source_image_ids`, kinds `table_atomic`/`figure_caption`), `compute_chunk_id` = sha256 over canonical JSON of `(doc_id, text, source_block_ids)`.
- `tokenize.py` — `TokenCounter` (pinned bge-m3 BPE via `tokenizers`, `sha256(tokenizer.json)` ref hash; char/4 hermetic fallback; actual mode exposed).
- `sentences.py` — deterministic `split_sentences` (final-punct + ws/capital/digit, CJK-direct rule, abbreviation/initial guard, `split_ambiguous`) and `tail_sentences` (final complete sentences bounded by budget).
- `chunker.py` — `SemanticChunker.chunk()` pure function: order resolution (chain → page fallback → orphans), heading-starts-chunk, band merge, oversized sentence-split (≤ target), degenerate forced-split with recursive separator fallback, heading anchor inheritance, heading-seam-only overlap with `overlap_source_chunk_id` attribution, full report contract.
- `batching.py` — `group_by_token_budget` greedy order-preserving, both caps, over-budget chunk isolated.
- `store.py` — `ChunkStore` ABC + `FilesystemChunkStore` (versioned keys, numeric-latest, deterministic overwrite, versions retained, `iter_*`, `get_embedding`, local `_version_suffix`, `_sanitize_embedder_id`).
- `pipeline.py` — `ChunkEmbedPipeline` (resolve latest normalized DOM → chunk → persist → embed-only-missing → merge to artifact order → validation stamp → rewrite `embedding_ref` → `chunk_embedded.v1` event); `chunk_only()` helper for the CLI.
- `cli.py` — thin `python -m app.chunking.cli --doc <id> --store <root> [--embed] [--dom-key <key>]`.
- `__init__.py` — module docstring (trust boundary), `__version__`, full public surface.

### Track C — real-BGE gated tests

- `tests/test_chunk_embed_pipeline_real.py`: `_available()` gated on torch/sentence-transformers + `models/bge-m3/{tokenizer,config}.json`. `test_cosine_stable_across_runs` (two independent stores, cosine ≥ 0.9999), `test_dim_and_dtype` (1024, float32, (2,1024)), `test_name_identity`, `test_never_embeds_twice_real`.

## 2. Test results

```
pytest tests/ -q  →  94 passed, 1 skipped, 3 warnings  (46.08 s)
```

- Baseline at start: 34 passed, 1 skipped (docling fallback skip — unchanged).
- **+60 new tests** (28 `test_chunking.py`, 9 `test_chunk_store.py`, 6 `test_batching.py`, 12 `test_chunk_embed_pipeline.py`, 4 `test_chunk_embed_pipeline_real.py`, +1 `test_sbert_embedder.py`).
- All hermetic via `TokenCounter(mode="char4")` + `DummyEmbedder`; the real-model tests run on this box (CUDA + local BGE-M3 verified: `torch 2.13.0+cu126`, `cuda True`).

## 3. Deviations from the plan + why

1. **`_sanitize_embedder_id` maps `/` → `__`** (Fact: plan B7 test + Flag #2 both require `"BAAI/bge-m3@local-fp16"` → `"BAAI__bge-m3_local-fp16"`). A uniform one-char→one-underscore rule yields `BAAI_bge-m3_local-fp16` (one underscore), which contradicts the plan's explicit expected value; the plan's own Flag #2 resolution keeps the double underscore. Implemented `/` → `__` (path-separator-safe double separator), all other disallowed chars → `_`. Matches the plan's test exactly; flagged in case the architect prefers a uniform rule.
2. **D1 (`project_memory/module_status.md`) deferred.** The orchestrator's task message explicitly forbids touching `project_memory/` (write scope is `app/`, `tests/`, and `checkpoints/run/.../engineer-report.md`). The Knowledge Curator should append the Module #3 row (chunker version `chunker-v0.1.0`, token-budget caps, `ChunkStore` seam, BGE-M3 wiring, ADR-009/010, +60 test delta). Append, never destroy.
3. **`chunk_only()` public method added** to `ChunkEmbedPipeline` (small extension of §3.8) so the CLI's "without `--embed`" mode does not reach into private pipeline internals.
4. **Sentence splitter adds a CJK-direct boundary rule** (final punct directly followed by a CJK ideograph splits even without whitespace). The plan's literal wording requires whitespace after punctuation, but CJK text has no inter-sentence whitespace; the rule keeps the CJK case (`。`/`！`) genuinely useful and is deterministic. Hermetic tests cover both spaced and unspaced CJK.
5. **`test_sbert_embedder._available()` also checks `models/bge-m3/config.json`** so the bge-m3 fixture skips (not downloads) on model-less machines.

## 4. Bugs found and fixed during implementation

- `app/chunking/store.py`: `_emb_paths` originally used `Path.with_suffix` on a dotted base (`emb-v0.1.0-…`), which mangled the filename to `emb-vv0.1…` and truncated the version. Rebuilt filenames explicitly.
- `app/chunking/store.py`: version template was `emb-v{version}` with `version` already carrying the leading `v` (suffix form) → double `v`; now `emb-{version}`.
- `app/chunking/pipeline.py`: the full-matrix row assembly used `existing_map.get(c.chunk_id, new_map[c.chunk_id])` — `dict.get` evaluates the default eagerly, so the never-embed-twice re-run (empty `new_map`) raised `KeyError`. Replaced with an explicit membership branch. (This was a real never-embed-twice regression the tests caught.)
- `app/chunking/chunker.py`: `_force_split` infinite-recursed when the only separator occurrence was trailing (cut == len(text) produces no split); filtered cuts to `0 < c < len(text)`. Oversized re-accumulation now budgets on the actual joined text count (join overhead was under-counted), so sub-chunks are genuinely ≤ `target_tokens`.

## 5. Verification on the box (Fact)

- `pytest tests/ -q` → 94 passed, 1 skipped.
- Real CLI smoke: `python -m app.chunking.cli --doc cli-smoke --store <tmp> --embed` → `OK cli-smoke chunks=2 embedded=2 skipped=0 dim=1024 dtype=float32 embedder=BAAI/bge-m3@26159e7a-fp16`, emb keys `emb-v0.1.0-BAAI__bge-m3_26159e7a-fp16.{json,npy}`. This also validates A1's `name` on the real model path.

## 6. Deferred / flagged

- D1 memory update (Knowledge Curator, see deviation #2).
- Flag #1 (DOM resolution glob stays inside `pipeline._resolve_dom`, no `Store` ABC change) and Flag #3 (`batch_embed` default 64 untouched) from the plan remain as flagged — no design change made.
- Vector store / hybrid retrieval (`ChunkStore.iter_*` is the seam), atomic table/figure-caption chunks, parent-child context injection — out of scope this run (architecture §8).

---

## 7. Round-1 fix (reviewers' majors + cheap minors) — 2026-08-05

Both reviewers PASSed and routed back 3 majors + 7 cheap minors. Fixed the 3 majors and the 4 requested cheap minors only (reviewers' remaining minors are acknowledged latent/cosmetic and left untouched per scope). Suite: **99 passed, 1 skipped, 2 warnings** (was 94/1/3 — the new pydantic v2 `ChunksArtifact.schema` shadow warning is gone; +5 regression tests).

### MAJOR 1 — `chunk_id` collision on byte-identical oversized pieces (PASS-level, reviewed)
- **Root cause (Fact):** `compute_chunk_id` hashed `(doc_id, text, source_block_ids)`. A single >2048-token block of repeated identical sentences yields sentence-sub-chunks with byte-identical text → identical `chunk_id`s in one artifact; the same applies to a force-split degenerate sentence whose halves are identical (`"Y"*8400` → `"Y"*4200` ×2). This broke the never-embed-twice key (`pipeline.py:161`) and `get_embedding` (`store.py:178`).
- **Fix (preferred discriminator, not assert):** `app/chunking/schema.py:66-94` — `compute_chunk_id(..., piece_index: int | None = None)`; `piece_index` is added to the canonical JSON only when not `None` (ordinary chunks keep byte-identical ids → existing stored embeddings stay valid). `app/chunking/chunker.py:121-131` enumerates oversized/forced pieces and threads `piece_index=piece_idx`; `chunker.py:324,344` pass it through `_build_chunk` → `compute_chunk_id`. Docstrings updated (`schema.py:1-8`, `chunker.py:11-14`).
- **Regression tests:** `tests/test_chunking.py` `test_oversized_repeated_sentences_distinct_chunk_ids` (170 identical sentences → all pieces distinct + precondition assert that texts ARE byte-identical) and `test_forced_split_identical_pieces_distinct_chunk_ids` (`"Y"*8400` → identical halves, distinct ids).

### MAJOR 2 — `_version_suffix` duplication (Jaccard 1.00)
- **Fix:** `app/chunking/store.py:25` imports the pure function from `app.parser.storage`; the local byte-identical copy (old `store.py:49-54`) is deleted. Module docstring (`store.py:11-14`) updated. `app/parser/storage.py` untouched. `scripts/check_similarity.py` now reports only the pre-existing out-of-scope `ocr.py`/`docling_loader.py` pair.

### MAJOR 3 — `SentenceTransformerEmbedder` default `batch_size`
- **Fix:** `app/embedding/sbert.py:76` — constructor default `128` → `32` (matches the A3 change reviewers expected; chunk path's explicit sizing in `pipeline._run_ok` untouched).

### MINOR 4 — eager embedder → lazy
- **Fix:** `app/chunking/pipeline.py:61` stores `self._embedder`; new lazy property `pipeline.py:72-82` calls `default_embedder()` on first `self.embedder` access. `chunk_only()` / CLI-without-`--embed` never touch the property, so BGE-M3 is never loaded into VRAM for chunk-only runs. CLI (`cli.py:34`, embed branch) still resolves it after `run()`.
- **Regression test:** `tests/test_chunk_embed_pipeline.py` `test_chunk_only_never_touches_embedder` — monkeypatches `default_embedder` to raise; `chunk_only()` succeeds and `_embedder` stays `None`.

### MINOR 5 — `ChunksArtifact.schema` shadow
- **Fix:** `app/chunking/schema.py:60` — field renamed `schema` → `schema_version` (default `"chunks-v1"`). Updated constructors `app/chunking/pipeline.py:112,147` and assertion `tests/test_chunking.py:127`. Old artifacts on disk (`"schema"` key) still load: extra field ignored, `schema_version` defaults. Pydantic v2 warning gone (2 warnings now, down from 3).

### MINOR 6 — decimal false-splits
- **Fix:** `app/chunking/sentences.py:47-66` new `_decimal_guard` — a `.` directly after a digit that is part of an alphanumeric run (`BP120/80.`, `Version2.0.`) is not a sentence boundary; plain numbers still end sentences (`"The dose is 45. He improved."`). Wired into `split_sentences` (`sentences.py:103-107`), counted toward `split_ambiguous`, fully deterministic. Module docstring updated.
- **Tests:** `tests/test_chunking.py` `test_decimal_false_split_guard` + `test_pure_number_sentence_end_still_splits`.

### MINOR 7 — factory docstring
- **Fix:** `app/embedding/factory.py:9-11` — replaced the "downloads to local HF cache on first use" claim with the on-prem posture: the real model loads from the local `models/bge-m3` copy (fetched ahead of time by `scripts/download_models.py`), no runtime HF fetch.

### Verification on the box (Fact)
- `pytest tests/ -o addopts="" --color=no --tb=no` → **`99 passed, 1 skipped, 2 warnings in 45.35s`** (exit 0).
- `scripts/check_similarity.py` → 1 pre-existing out-of-scope pair only; `_version_suffix` duplicate resolved.
- Targeted: `tests/test_chunking.py test_chunk_store.py test_chunk_embed_pipeline.py test_batching.py -k "distinct or decimal or pure_number or never_touches or schema or oversized or forced or roundtrip or chunk_only"` → 12 passed.
- Not touched: `project_memory/`, `docs/`, `app/parser/storage.py`, and none of the reviewers' other minors (acknowledged latent `_version_key` int/str mix, overlap-prefix budget nuance, double artifact write, sidecar `normalize` assertion, edge-report semantics, `get_embedding` full-matrix seam).
