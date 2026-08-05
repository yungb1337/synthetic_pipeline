# Deferred Issues — run-2026-08-04-audit (fix round 2)

Tracked issues deferred to a future run. The Knowledge Curator promotes these to `project_memory/questions.md` at checkpoint.

1. **Manifest full-rewrite O(n²) at millions-scale** — the dirty-flag (`executor.py` `_flush`) skips unchanged rewrites, but every *dirty* flush still rewrites the whole sorted manifest, so at millions-scale the batch is O(n²) in disk I/O; deeper fix (incremental/segment manifest or append-only journal) is a design change, not this round.
2. **Failed/empty-sha docs are re-parsed every run (no dead-letter)** — empty-sha refs are intentionally kept pending (`corpus.pending`) so unhashable files surface as failures, but nothing records that a doc already failed; a re-run re-attempts every failed/empty-sha doc — dead-letter/backoff design decision needed.
3. **Loader registry (if/elif → registry)** — `Loaders.load()` is still a hard-coded if/elif chain per slug (spec §8 / universal §11); refactor to a registry so new formats are additive.
4. **PDF OCR fallback for scanned pages** — the PDF loader has no OCR fallback for scanned page images (ADR #4 covers standalone image files only); needs OCR-engine integration.
5. **`ocr_warm` eager RapidOCR build** — `ProcessingConfig.ocr_warm` preloads RapidOCR even for text-only corpora; wire it to actually matter or gate it.
6. **`tests/test_sbert_embedder.py` uses `bge-small-en-v1.5`** — the module-scope fixture loads a small model instead of the product embedder (`bge-m3`, 1024-dim); also non-hermetic (network download when uncached).
