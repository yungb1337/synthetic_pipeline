# Checkpoint — run-2026-08-04-chunking (Module #3: Semantic Chunking + BGE-M3 wiring)

**Status: COMPLETE** · 2026-08-05 · Suite: **99 passed / 1 skipped** (exit 0, baseline 34/1) · Both reviewers PASS.

## Objective met
Module #3 — **Semantic Chunking** — built as a decoupled projection in `app/chunking/`: a normalized DOM is turned into **content-addressed, lineage-carrying chunks** (the retrieval atom) and projected to embeddings through the existing `Embedder` protocol. BGE-M3 is wired as the **product embedder** with an identity-bearing `name`; batch defaults are lowered to the 4 GB fp16 envelope. This is the **retrieval-grounding seam**: `ChunkStore.iter_*` is what downstream retrieval (the "Unknown → trusted-source retrieval → evidence" loop) will consume. Full run brief: `project_memory/active_objective.md`.

## Per-gate artifacts
1. **Research** — `RESEARCH: COMPLETE` → `research.md` (Q1–Q4: DOM-anchored chunking; ~400/2048/48 size-overlap; content-addressed lineage; token-budget batching; 4 open risks recorded).
2. **Architecture** — `ARCHITECTURE: APPROVED` → `architecture.md` (trade-off review for every major choice; 4 open risks resolved). ADRs **009 + 010** written.
3. **Plan** — `PLAN: READY` → `implementation-plan.md` (16 tasks, 3 tracks; 5 flags for the architect).
4. **Engineer** — implemented → `engineer-report.md` (Track A embedding follow-ups, Track B `app/chunking/` B0–B10, Track C real-BGE gated tests).
5. **Architecture review** — `VERDICT: PASS` (`reviews/architecture.md`; 1 major + 6 minors routed back).
6. **Quality & performance review** — `VERDICT: PASS` (`reviews/quality.md`; 2 majors + 7 minors routed back).
7. **Fix round 1** (2026-08-05) — 3 majors + 4 cheap minors fixed; suite 94 → **99** (+5 regression tests).
8. **Checkpoint** — this file. (Final report `final-report.md` is out of scope for this checkpoint; run DoD item #4.)

## Fix round 1 (reviewers' majors + requested cheap minors)
- **MAJOR 1 — `chunk_id` collision on byte-identical oversized pieces:** `compute_chunk_id(..., piece_index: int | None)` (`schema.py`) discriminates sentence-split/force-split pieces of ONE oversized block; ordinary chunks keep the pure `{doc_id, text, source_block_ids}` identity (stored embeddings stay valid). 2 regression tests (`test_oversized_repeated_sentences_distinct_chunk_ids`, `test_forced_split_identical_pieces_distinct_chunk_ids`).
- **MAJOR 2 — `_version_suffix` duplication (Jaccard 1.00):** local copy deleted; `app/chunking/store.py:25` imports the pure function from `app.parser.storage` (chunking already depends on `app.parser.dom`). Similarity signal clean (only the pre-existing `ocr.py`/`docling_loader.py` pair). Follow-up: promote to a public helper — tracked in [[questions]].
- **MAJOR 3 — `SentenceTransformerEmbedder` ctor `batch_size=128`:** aligned to 32 (completes the A3 default-lowering; direct construction no longer advertises the OOM-risky default).
- **MINOR 4 — eager embedder → lazy:** `ChunkEmbedPipeline` builds the embedder on first access, so `chunk_only()` / CLI-without-`--embed` never load BGE-M3 into VRAM.
- **MINOR 5 — `ChunksArtifact.schema` → `schema_version`:** pydantic v2 shadow warning gone; old on-disk artifacts still load.
- **MINOR 6 — decimal false-splits guarded:** `_decimal_guard` in the sentence splitter (`BP120/80.`, `Version2.0.`); plain numbers still end sentences.
- **MINOR 7 — factory docstring on-prem posture:** no runtime HF fetch claim corrected.
- Reviewers' remaining minors acknowledged latent/cosmetic and left untouched per scope (recorded in `engineer-report.md` §7).

## Final verdicts & test status
- **Architecture Reviewer: `VERDICT: PASS`** · **Quality & Performance Reviewer: `VERDICT: PASS`**
- `.venv/Scripts/python.exe -m pytest tests/ -o addopts="" --color=no --tb=no` → **99 passed, 1 skipped, 2 warnings** (45.35 s). Baseline 34/1; **+60 new tests** (28 chunking, 9 chunk_store, 6 batching, 12 pipeline, 4 real-BGE, 1 sbert) + **5 round-1 regression tests**.
- Real-path smoke (Fact): CLI `--embed` on a real DOM → `chunks=2 embedded=2 skipped=0 dim=1024 dtype=float32 embedder=BAAI/bge-m3@26159e7a-fp16`, emb keys `emb-v0.1.0-BAAI__bge-m3_26159e7a-fp16.{json,npy}` — validates A1's identity-bearing `name` on the real model path.

## ADRs added
- **ADR-009** — Semantic Chunking module: DOM-anchored, content-addressed chunks (boundary strategy, `chunk_id` rule + round-1 `piece_index` amendment, overlap policy, tokenizer pinning, storage keys, `ChunkStore` seam, `ChunkEmbedPipeline` never-embed-twice + token-budget batching, embedder-identity tightening, default lowering, tables/figures out of scope).
- **ADR-010** — fp16 determinism policy: cosine-stable equality (cosine ≥ 0.9999) for GPU-fp16 embeddings; bit-exact CPU/`DummyEmbedder`; per-artifact validation stamp; strict mode kept as opt-in for audits.

## Tracked follow-ups (non-blocking, → `project_memory/questions.md` "Tracked from run-2026-08-04-chunking")
1. Promote `_version_suffix` to a public `version_suffix`/shared helper before any parser refactor (`app/chunking/store.py:25` imports the private name).
2. Atomic table-chunk and figure+caption-chunk seams (tables/images live outside `Block.text`) — documented next step, schema-reserved.
3. Oversized-piece re-embed note — positional `piece_index` means a content-neutral edit can shift later pieces' ids → one-time re-embed on upgrade.
4. Pipeline-level never-embed-twice test for oversized docs (currently covered at the chunker level).

## Process notes
This is the first forward run executed as a complete research → architecture → plan → engineer → reviews → **post-review fix round** → checkpoint chain (the docling run had no formal review fix round; the audit run was backward-looking) — the round-1 review caught the `chunk_id` collision and the sbert batch-default half-fix, both real and now pinned by regression tests, before either could bite in production.
