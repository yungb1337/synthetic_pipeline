---
name: module-status
description: Live status of platform modules and what is being worked on now
metadata:
  type: project
---

# Module Status

**Phase:** Module #1 — the Parser ("Document / Extraction Pipeline") — design in progress, not yet coded.

## The intended build order (dependency-first, per brief)
1. **Parser / Document Extraction Pipeline** ← **NOW**
2. Text Normalization & Cleaning
3. Semantic Chunking
4. Embeddings
5. Knowledge extraction (Entity/Rel) → Ontology mapping → KG
6. Validation framework (multi-stage)
7. Generation (Prompt builder + LLM + multi-generator)
8. Dataset versioning / lineage / compiler
9. Multi-tenancy, security, governance
10. Delivery APIs / dashboard / observability

## Current status
- [x] Read source material (SYN1-4). Papers-pending (4 PDFs).
- [x] Project memory scaffold (this + master_context + architecture_decisions + reading_notes + MEMORY index).
- [x] Parser module design spec → `docs/parser-module-spec.md`
- [x] **Confirm tech stack** (PyMuPDF · RapidOCR · Extraction→DOM · Store-abstraction)
- [x] **Implement parser (Extraction→DOM)** — venv + all modules
- [x] **Tests green (7)** — detection/CSV/MD/PDF/idempotency/unsupported/store
- [x] **Checkpoint 001** → `checkpoints/checkpoint_001.md`
- [x] **Module #2 Normalizer** implemented+tested → `app/normalizer/`, `docs/normalizer-module-spec.md` (18 tests green)
- [x] **Batch/scale layer** → `app/processing/` + batched OCR + batching-capable embeddings seam → `docs/scale-batch-spec.md` (24 tests green; ~2ms/doc, incremental manifest verified)
- [x] **Real local GPU embeddings** → `SentenceTransformerEmbedder` = **`BAAI/bge-m3` (1024-dim, multilingual)** on RTX 3050 (torch 2.13+cu126, sentence-transformers 5.6, fp16 to fit VRAM); model in `models/bge-m3` via `scripts/download_models.py`; `factory.default_embedder` auto GPU/CPU, Dummy fallback
- [x] **Git initialized** (commit f5a72ec) — source/docs/memory/tests tracked; `.venv`, `models/`, caches ignored
- [x] **Audit run `run-2026-08-04-audit` COMPLETE (2026-08-04)** — Architecture + Quality reviewers both **PASS** after 2 fix rounds; suite **27 → 31 green**; similarity signal clean (no pairs ≥ 0.40). Key hardening: batch-layer self-deadlock fixed, `.txt` loader fixed, content-hash image keys, versioned DOM storage (`dom/{doc_id}/dom-v{version}.docJSON`), idempotent normalizer (`_WS_RE`), manifest dirty-flag. 6 design items deferred → tracked in [[questions]].
- [x] **Docling backend run `run-2026-08-04-docling` COMPLETE (2026-08-04)** — ADR-007: **Docling 2.118.0 integrated as a GATED layout/table engine** (`ParserConfig.layout_backend`: `"native"` default, `"docling"` opt-in, auto-engages where layout is required). On the Docling path it **replaces** the heuristic reading order + PyMuPDF `find_tables`; DOM/harness/events/storage/trust boundary untouched. `app/parser/loaders/docling_loader.py` (lazy engine, on-prem cache `models/docling` ~506M git-ignored). Provenance records `docling_version`/`layout_model`; reading order authoritative on that path. Env fixes baked in: `TORCHDYNAMO_DISABLE` (no Triton on Windows/Py3.14), `iterate_items()` tuple unpacking. Suite **35 passed / 1 skipped** green; both reviewers PASS.
- [x] **Semantic Chunking run `run-2026-08-04-chunking` COMPLETE (2026-08-05)** — new **`app/chunking/`** module (Module #3): DOM-anchored **content-addressed chunks** (~400-token target / 2048 hard cap, 48-token heading-seam overlap), **`ChunkStore`** seam (interface-only — the **retrieval-grounding seam**), **`ChunkEmbedPipeline`** (never-embed-twice keyed on `chunk_id`, token-budget batching ≤16k tokens / ≤32 texts), **BGE-M3 wired as the product embedder** with identity-bearing `name` (`BAAI/bge-m3@<rev>-fp16`), batch defaults lowered 128/64 → 32 (embedding factory + processing config + `SentenceTransformerEmbedder` ctor). ADR-009 + ADR-010; fix round 1 folded a positional `piece_index` into oversized-piece identity (chunk_id collision fix). Suite **99 passed / 1 skipped** (baseline 34/1). Retrieval-grounding seam delivered — Module #4 (retrieval) is next.
- [ ] Archive the academic-PDF read (re-dispatch) → ground Ontology/KG/Ontology decisions

## Next module
**Module #3 — Semantic Chunking** (own module, own spec), then wire the real embedder into the chunk→embed pipeline.

**Module #4 — retrieval next** (updated 2026-08-05, run-2026-08-04-chunking COMPLETE): `ChunkStore.iter_*` (`app/chunking/store.py`) is the retrieval seam; a vector store (pgvector/Qdrant) + hybrid BM25+dense behind it is a future ADR. Documented next steps from this run: atomic table/figure-caption chunks, parent-child context injection (both schema-reserved).

## Blocked by
Nothing for the parser itself. Folder rename (22_07 → synthetic_pipeline) blocked until editor releases CWD.