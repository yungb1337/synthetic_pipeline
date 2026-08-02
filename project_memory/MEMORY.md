# Project Memory Index

Persistent checkpoint + context files for this project.

- [Master Context](master_context.md) — the one-sentence mission, the trust thesis, guardrails.
- [Architecture Decisions](architecture_decisions.md) — parser scope + accepted decisions from SYN1–4.
- [Reading Notes](reading_notes.md) — what was actually read, deepest takeaways per source, and source contradictions (incl. honesty note: 4 papers still pending a real read).
- [Module Status](module_status.md) — build order + live status (Parser/Normalizer/batch done; real GPU embeddings).
- [Questions](questions.md) — open decisions gating code (stack now resolved; the deferred KG contradiction).
- **Docs (design):** `docs/parser-module-spec.md` (Module #1) · `docs/normalizer-module-spec.md` (Module #2) · `docs/scale-batch-spec.md` (batch/throughput) · `docs/universal-document-understanding-engine.md` (earlier platform design).
- **GPU/local models:** `requirements-gpu.txt` (torch cu126 + sentence-transformers) · `scripts/check_embedder.py` · `app/embedding/sbert.py`.
- **Checkpoint:** `checkpoints/checkpoint_001.md` (Module #1). Module #2 + batch + GPU embeddings done (27 tests green; GPU embedder runs on CUDA).
- **Research sources:** `_research_sources/*.txt` (extracted text of SYN1–4 + papers).

Principle: append, never destroy; keep every decision's reasoning.