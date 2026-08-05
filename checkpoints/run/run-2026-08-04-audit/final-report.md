# Final Report — run-2026-08-04-audit

**Objective:** audit the existing modules (`app/parser`, `app/normalizer`, `app/processing`, `app/embedding`) for architecture alignment, code duplication, and quality/performance — and fix what surfaced.

**Result:** ✅ Both reviewers **PASS** · pytest **31 green** (was 27) · duplication signal **clean** · smoke driver green.

## What the audit found and fixed (2 fix rounds)

### Blocking bugs (reproduced, then fixed)
- **Batch-layer self-deadlock** — `_flush()` re-acquired the non-reentrant lock while held; any batch crossing the 256-doc flush boundary hung. Lock discipline fixed; new 300-doc boundary test.
- **Missing `.txt` loader** — every plaintext file crashed the parser (`_plain` didn't exist). Wired to the real text loader; new txt test.

### Major hardening
- **Nondeterministic image storage** — `put_image` keys from a live glob count → now content-hash keys (`images/{doc_id}/{sha256}.{ext}`), idempotent.
- **DOM versions destroyed** — `put_dom`/`put_normalized` single-slot overwrites → now versioned `dom/{doc_id}/dom-v{version}.docJSON`; ADR #8 honored.
- **Whole-file hashing into RAM** — shadowed dead chunked `_hash_file` removed; streaming 1 MiB path active.
- **Double read/hash per doc** — `extract` now takes the precomputed sha256.
- **Dead limit knobs** — `max_file_bytes` enforced; dead `ParseLimits`/`max_pages_for_ocr` removed.
- **Silently-skipped failed files** — unhashable files now surface as failures.
- **Manifest O(n²) mitigation** — dirty-flag skips unchanged rewrites (deeper fix tracked).
- **Normalizer idempotency regression** — the round-1 `_WS_RE` rewrite broke "second pass is a no-op"; restored + regression test (the review caught our own bug — the loop works).

### Cleanups
- Duplication eliminated: `put_dom`/`put_normalized` (Jaccard 1.00) consolidated; `_MIME` maps merged into one `app/parser/mime.py`; dead code removed (`BlockKind`, `to_json_bytes`); event names aligned with spec §11 (`document.parse_failed`); deterministic failures no longer retried; public `silent_sink()` instead of private `_silent`.

## Process notes (first org run)
- Gate pipeline worked end-to-end: audit → fix → re-review → checkpoint, 2 rounds to PASS.
- The re-review caught a regression *our own round-1 fix introduced* (idempotency) — the read-only reviewer + verified claims paid off.
- Reviewers are read-only, so the Orchestrator transcribed their verdict artifacts. Next run: grant reviewers scoped `Write` to `reviews/`.
- 6 items deferred and tracked in `project_memory/questions.md` (manifest O(n²) at millions-scale, dead-letter for bad files, loader registry, PDF OCR fallback, `ocr_warm`, sbert test model).

## Where the project stands
- Module #1 Parser, #2 Normalizer, batch/scale layer, GPU embeddings: hardened by this audit.
- Next module remains **Module #3 — Semantic Chunking** (see `project_memory/module_status.md`).
