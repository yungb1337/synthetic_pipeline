---
name: active-objective
description: The run brief — the objective and notes the autonomous organization executes next. Overwrite the body for each new run; never delete the file.
metadata:
  type: project
---

# Active Run Brief

> The Project Orchestrator (`/dev-team` in-session, or the `audit` workflow in background)
> reads this file at the start of a run. Replace the contents for a new run; keep this file.

## Run id
`run-2026-08-04-chunking`

## Objective
**Module #3 — Semantic Chunking**, plus wiring the real BGE-M3 embedder into the chunk→embed pipeline. Build the retrieval-grounding seam: turn normalized DOM blocks into embeddable, lineage-carrying chunks that downstream retrieval (the "Unknown → trusted-source retrieval → evidence" loop) will consume.

## Scope
- **New module `app/chunking/`**: convert normalized DOM blocks (`Block.text` from `app/normalizer`) into chunks — the retrieval atom.
- **Chunking strategy**: research + architect must compare fixed-size vs semantic vs recursive/windowed strategies and lock one with a documented trade-off review. Chunk boundaries must not split tokens mid-word/mid-sentence where avoidable.
- **Embedding wiring**: embed chunks in batches through the existing `app/embedding/` seam (`Embedder` protocol, `batch_embed`, BGE-M3 1024-dim via `SentenceTransformerEmbedder`; Dummy fallback stays). Model-sized batches, shape-guard.
- **Chunk metadata + lineage**: each chunk carries `doc_id`, source `block` reference(s), sequence index, and a versioned storage key — so every chunk is reproducible to its source bytes (provenance chain: doc → DOM → block → chunk → embedding).
- **Storage**: persist chunks + embeddings with versioned/deterministic keys, following the pattern already established in `app/parser/storage.py` (`raw/<sha>`, `dom/{doc_id}/dom-v{ver}`, `images/{doc_id}/{sha}`).
- **Retrieval seam (interface only this run)**: a chunk lookup/iteration interface that downstream retrieval can build on — no full vector search this run.

## Constraints
- Trust boundary preserved: idempotent, deterministic, faithful (None, never fabricated), provenance recorded (chunker version, embedder model, params).
- Modular monolith; reuse the `Embedder` protocol and storage patterns; do NOT redesign parser/normalizer/processing.
- Existing suite must stay green (`31 + docling 35` baseline) — chunking must not break parser/normalizer/processing/docling paths.
- On-prem: embeddings run on the local GPU/CPU; no telemetry.
- Append, never destroy (memory + ADRs).

## Definition of done
1. Architecture Reviewer and Quality & Performance Reviewer both emit `VERDICT: PASS`.
2. `.venv/Scripts/python.exe -m pytest tests/ -q` is green (new chunking tests + existing suite).
3. Knowledge Curator checkpoint at `checkpoints/run/run-2026-08-04-chunking/checkpoint.md`.
4. Final report at `checkpoints/run/run-2026-08-04-chunking/final-report.md`.

## Notes for the team
- Research grounding is already in `project_memory/reading_notes.md` (papers read 2026-08-04): retrieval-grounding reduces hallucination; chunks are the retrieval atom; BGE-M3 is the product embedder (1024-dim, multilingual).
- Prior run `run-2026-08-04-docling` (gated Docling layout engine) is COMPLETE — parser layout path is Docling-backed when gated on; chunking operates on the normalized DOM and should not depend on which layout backend produced it.
