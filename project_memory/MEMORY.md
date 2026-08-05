# Project Memory Index

Persistent checkpoint + context files for this project.

- [Master Context](master_context.md) — the one-sentence mission, the trust thesis, guardrails.
- [Architecture Decisions](architecture_decisions.md) — parser scope + accepted decisions from SYN1–4.
- [Reading Notes](reading_notes.md) — what was actually read, deepest takeaways per source, and source contradictions (incl. honesty note: 4 papers still pending a real read).
- [Module Status](module_status.md) — build order + live status (Parser/Normalizer/batch done; real GPU embeddings).
- [Questions](questions.md) — open decisions gating code (stack now resolved; the deferred KG contradiction).
- **Docs (design):** `docs/parser-module-spec.md` (Module #1) · `docs/normalizer-module-spec.md` (Module #2) · `docs/scale-batch-spec.md` (batch/throughput) · `docs/universal-document-understanding-engine.md` (earlier platform design).
- **GPU/local models:** `requirements-gpu.txt` (torch cu126 + sentence-transformers) · `models/bge-m3` (BGE-M3 1024-dim) · `scripts/download_models.py` · `scripts/check_embedder.py` · `app/embedding/sbert.py`.
- **Checkpoint:** `checkpoints/checkpoint_001.md` (Module #1). Module #2 + batch + GPU embeddings done (27 tests green; GPU embedder runs on CUDA).
- **Run checkpoint (first org run):** `checkpoints/run/run-2026-08-04-audit/checkpoint.md` — audit of parser/normalizer/processing/embedding; both reviewers PASS after 2 fix rounds; suite 31 green; 6 design items deferred → tracked in [Questions](questions.md).
- **Run checkpoint (Docling backend):** `checkpoints/run/run-2026-08-04-docling/checkpoint.md` — ADR-007: Docling 2.118.0 as a GATED layout/table engine (`layout_backend="native"` default / `"docling"` opt-in), replacing heuristic ROG + `find_tables` on that path; DOM/harness/trust boundary untouched; models cached on-prem `models/docling`; suite 35 passed/1 skipped. Next: resume Module #3 (chunking).
- **Run checkpoint (Semantic Chunking):** `checkpoints/run/run-2026-08-04-chunking/checkpoint.md` — Module #3 `app/chunking/` COMPLETE: DOM-anchored content-addressed chunks (~400/2048/48, heading-seam overlap), `ChunkStore` retrieval seam (interface-only), `ChunkEmbedPipeline` (never-embed-twice, token-budget batching ≤16k/32), BGE-M3 as product embedder with identity-bearing `name`, batch defaults lowered to 32, ADR-009 + ADR-010 (fp16 cosine-stable determinism); suite 99 passed/1 skipped. This is the retrieval-grounding seam — Module #4 (retrieval) next.
- **Research sources:** `_research_sources/*.txt` (extracted text of SYN1–4 + papers).

## The engineering organization
- [Active Run Brief](active_objective.md) — the objective the autonomous org executes next.
- **Org:** 8 agents in `.claude/agents/` · gates in `docs/org-gate-protocol.md` · drive with `/dev-team` (in-session) or the `audit` workflow (background) · similarity signal = `scripts/check_similarity.py`.

Principle: append, never destroy; keep every decision's reasoning.