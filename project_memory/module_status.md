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
- [x] **Real local GPU embeddings** → `app/embedding/` `SentenceTransformerEmbedder` (`BAAI/bge-small-en-v1.5`, 384) on RTX 3050 (torch 2.13+cu126 + sentence-transformers 5.6); `factory.default_embedder` auto GPU/CPU, Dummy fallback → `requirements-gpu.txt`, `scripts/check_embedder.py` (27 tests green on CUDA)
- [ ] Archive the academic-PDF read (re-dispatch) → ground Ontology/KG/Ontology decisions

## Next module
**Module #3 — Semantic Chunking** (own module, own spec), then wire the real embedder into the chunk→embed pipeline.

## Blocked by
Nothing for the parser itself. Folder rename (22_07 → synthetic_pipeline) blocked until editor releases CWD.