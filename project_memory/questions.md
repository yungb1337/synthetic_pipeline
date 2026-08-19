---
name: open-questions
description: Open decisions that block progress, tracked so nothing is invented
metadata:
  type: project
---

# Open Questions

## Parser-scoped (resolved)
1-7. Tech stack confirmed in-session: Python/FastAPI modular monolith, PyMuPDF, RapidOCR (on-prem), object-store abstraction w/ FS default, DOM JSON, job-worker layer. See architecture_decisions.md.

## RESOLVED (user decision, 2026-08-04)
**KG contradiction (SYN1/2 vs SYN3) — decided:** We MUST use the **Knowledge Graph as the grounded source of truth**; **Ontology is just as important as the KG** (keeps the graph sane as data grows). Embeddings/KG are complementary, not competing: KG = verified memory; embeddings = candidate retrieval to get unstructured text INTO the KG and to verify `Unknown`s; ontology = consistency.

## Tracked issues from run-2026-08-04-audit
Promoted from `checkpoints/run/run-2026-08-04-audit/deferred-issues.md` (fix round 2). Deferred design decisions — NOT blocking the parser/normalizer/batch pipeline (the audit's blocking/major/minor tiers were fixed in rounds 1+2); tracked here so nothing is invented or silently dropped. Full reasoning in `deferred-issues.md`; checkpoint in `checkpoints/run/run-2026-08-04-audit/checkpoint.md`.

- **Manifest full-rewrite O(n²) at millions-scale** — dirty-flag skips unchanged rewrites but every *dirty* flush still rewrites the whole sorted manifest → incremental/segment manifest or append-only journal is a design change for a future run.
- **Failed/empty-sha docs re-parsed every run (no dead-letter)** — empty-sha refs stay pending so unhashable files surface as failures, but nothing records a prior failure; a re-run re-attempts every failed/empty-sha doc → dead-letter/backoff design decision needed. **RESOLVED 2026-08-19** (run-2026-08-19-page-centric, ADR-013): the new `Ledger` (`manifest/<doc_id>/plan.json`) + `DocumentValidator` dead-letter mechanism records per-page `FAILED`/`DEAD` status and explicit `actual_vs_expected`; `Planner.plan(resume=True)` reschedules only `FAILED`/`DEAD` pages (`attempt+1`), never re-parsing pages already `OK` — idempotent resume + dead-letter now exist (see `app/parser/assembler.py`, `storage_pages.py`, `planner.py`).
- **Loader registry (if/elif → registry)** — `Loaders.load()` is still a hard-coded if/elif chain per slug (spec §8 / universal §11); refactor so new formats are additive.
- **PDF OCR fallback for scanned pages** — the PDF loader has no OCR fallback for scanned page images (ADR #4 covers standalone image files only); needs OCR-engine integration. **RESOLVED 2026-08-10** (run-2026-08-06-router, ADR-012): the **Enrichment** band now runs native extraction + OCR of no-text-block pages via the existing `ocr.ocr_bytes`, delivered in-place on the native `RecoveredDocument` (zero new OCR dependency). Lazy-load/fallback-to-native preserved; page/region selectivity reserved as a seam, not built (§16).
- **`ocr_warm` eager RapidOCR build** — `ProcessingConfig.ocr_warm` preloads RapidOCR even for text-only corpora; wire it to actually matter or gate it.
- **`tests/test_sbert_embedder.py` uses `bge-small-en-v1.5`** — module-scope fixture loads a small model instead of the product embedder (`bge-m3`, 1024-dim); also non-hermetic (network download when uncached). **RESOLVED 2026-08-05** (run-2026-08-04-chunking Track A2): fixture → `BAAI/bge-m3`, `_available()` also checks the local `models/bge-m3/config.json` (skips, does not download, on model-less machines), `test_name_identity` added.

## Tracked from run-2026-08-04-chunking (2026-08-05)
Non-blocking follow-ups surfaced by the reviewers / architect; the run is COMPLETE (99 passed / 1 skipped). Full reasoning: `checkpoints/run/run-2026-08-04-chunking/`.

- **Promote `_version_suffix` to a public shared helper** — `app/chunking/store.py:25` imports the **private** `_version_suffix` from `app.parser.storage`. Fix round 1 deleted the byte-identical duplicate (Jaccard 1.00) in favor of this import, but any future refactor of that private name in `app/parser/storage.py` silently breaks the chunking consumer. Promote to a public `version_suffix` (shared helper) before the next parser refactor.
- **Atomic table-chunk and figure+caption-chunk seams (documented next step)** — `Page.tables`/`Page.images` live outside `Block.text` and outside `reading_order`; this run chunks `Block.text` only. Schema already reserves `kind="table_atomic"|"figure_caption"` + `source_table_ids`/`source_image_ids` so the step lands without a schema bump. Next step: serialize the `Table` grid (header+rows) into an atomic chunk and bind image+caption.
- **Oversized-piece re-embed note** — `piece_index` is positional within an oversized block, not semantic: a content-neutral edit that shifts piece boundaries can change *later* pieces' ids → a one-time re-embed of the affected chunks on upgrade. Expected and bounded; keep on the radar when replacing chunks that came from oversized blocks.
- **Pipeline-level never-embed-twice test for oversized docs** — never-embed-twice is covered at the chunker level (distinct ids for byte-identical pieces) and the pipeline level for ordinary docs, but not end-to-end for a doc that *has* oversized pieces (re-run → only the new `chunk_id`s embedded). Cheap addition to `tests/test_chunk_embed_pipeline.py`.

## Tracked from run-2026-08-06-router (2026-08-10)
Non-blocking follow-ups surfaced by the reviewers / architect during calibration; the run is COMPLETE (159 passed / 1 skipped). Full reasoning: `checkpoints/run/run-2026-08-06-router/`.

- **Refine layout/reading-order/multi-column detectors** so complex-academic documents (e.g. MDPI electronics papers) **reliably reach the Docling band without over-flagging simple text**. Currently pinned at **Enrichment** as a known limitation — `layout_complexity`/`block_fragmentation` are kept light in `RoutingConfig` because they are computed from raw page geometry and over-flag clean multi-paragraph text (~0.75 for a 30-paragraph single-column doc). Heavier weighting would over-route simple docs. Re-tune in `app/routing/config.py` as the corpus grows (ADR-011 challenge).
- **Accepted reviewer minors (both reviews PASS; these are non-blocking):**
  1. `FastInspector` calls `get_text("dict")` + `_est_multi_column` calls `get_text("rawdict")` per page — **reuse one geometry pass** (single read).
  2. A **"docling" route still records `route="docling"` when the engine is unavailable and native ran** — record the *executed* tier rather than the intended one.
  3. **Thin per-detector test coverage for form / reading_order** — add targeted signal tests (§17 per-detector independence).
  4. **Confidence is coverage-only** (share of band-driving weight-mass measured) with **no agreement term** among strongest signals — a real confidence model (agreement, not just coverage) is a future `Scorer` improvement.
- **Hindi / multilingual OCR** — the OCR backend (`rapidocr-onnxruntime`) default is **EN/zh**; a **Devanagari-capable engine remains a tracked decision** for the Enrichment band on Devanagari-script (e.g. Hindi) source material. On hold; no current corpus need.

## Tracked from run-2026-08-19-page-centric (2026-08-19)
Non-blocking follow-ups surfaced by the reviewers / engineer during the fix loop; the run is COMPLETE (pytest 204 passed / 1 skipped, clean corpus 15/15, 0 bad_alloc, 0 silent loss; both reviewers PASS). Full reasoning: `checkpoints/run/run-2026-08-19-page-centric/`.

- **GPU path untested on this CPU box** — ADR-013's `ResourceGovernor` formula includes a `gpu_free/gpu_per_job` term, but it is **dormant**: this machine has no CUDA (`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` is set yet inert). The GPU-cap side of `derive_heavy_concurrency` has never been exercised; needs a CUDA-enabled box before trusting it. (ADR-013 challenge.)
- **Live pool shrink is not force-killed** — `periodic_recheck` (every 4 completed docs) re-derives `heavy_concurrency` **downward-only** and applies it by setting the private `_max_workers` attr on the live `ProcessPoolExecutor`; in-flight workers are **not** force-killed, so the live count tightens only for *future* submissions. Satisfies "no upward mid-flight surge + downward-only", but the exact semantics of shrinking a running pool are implementation-private and should be re-verified if doc-level pools ever grow/contract sharply. (Flagged by the architecture reviewer as residual/non-blocking.)
- **Bounded backpressure is satisfied by fixed-size pools only** — `G5` accepted (documented): in-flight docling work is bounded by `ProcessPoolExecutor(max_workers=heavy_concurrency)` and native by `ThreadPoolExecutor(max_workers=native_concurrency)`; no separate chunked-submission/backpressure layer was added (the fix-loop doc deemed the pool-size bound sufficient). Keep on radar if a corpus ever shows submission bursts exceeding the pool before results drain.