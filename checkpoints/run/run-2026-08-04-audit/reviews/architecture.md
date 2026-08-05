# Architecture Review — run-2026-08-04-audit

**Recorded by:** Project Orchestrator, from the Architecture Reviewer's report (reviewer is read-only).

## Verdict
**VERDICT: FAIL**

## What conforms
- Canonical DOM as single downstream contract (`app/parser/dom/models.py:130`) — ADR #1 ✓
- Reading-order graph in-memory, no Neo4j (`reading_order.py:3-6`, `models.py:137`) — ADR #3 ✓
- Layout/OCR/tables as extractors in one pass (`loaders.py:142-208`) — ADR #2 ✓
- Tables first-class, images extracted-not-analyzed — ADR #5/#6 ✓
- Modular monolith: parser imports only within itself — ADR #7 ✓
- Normalizer pure/idempotent projection, no domain lock-in — normalizer spec ✓
- No deferred decision (KG/ontology) locked in — consistent with resolved questions ✓

## Blocking
- **BLOCKING `app/processing/executor.py:126-137`** — `_flush()` called from `_record()` while the non-reentrant `threading.Lock` (line 89) is held; `_flush` re-acquires it (line 140). Any batch reaching the flush boundary (256 docs, `% _flush_every == 0`) deadlocks deterministically. Tests use ≤4 docs, so never exercised.

## Major
- `app/parser/storage.py:65-71` — `put_image` key from `len(glob(...))` run-history index, not content hash → nondeterministic keys + orphaned files; violates parser determinism + ADR #8 idempotency.
- `app/parser/storage.py:53-63` — `put_dom`/`put_normalized` single-slot overwrites; contradicts documented `dom/<doc_id>/dom-v{version}` layout, ADR #8 versioned outputs, spec §10.
- `app/parser/loaders/loaders.py:131-217` — PDF loader has no OCR fallback for text-empty/scanned pages; ADR #4 honored for images only. Scanned PDF → silent empty DOM.
- `app/processing/corpus.py:26` vs `:65` — `_hash_file` defined twice; second shadows first; active path is whole-file `read_bytes()` (RAM spike at scale).
- `app/parser/ocr.py:78` — `batch_ocr_bytes()` never invoked; spec scale-batch.md:23-24 overstates code (no caller).
- `app/processing/corpus.py:56-62,89-90` — `pending()` returns False for `sha256 == ""` → failed-hash files silently counted as skipped, never reported/retried.
- `app/parser/loaders/loaders.py:96-119` — format dispatch is if/elif chain, not the per-type registry spec §8 / universal §11 promise.

## Minor
- `app/parser/extraction.py:55,61` — events `document.unresolved`/`document.failed` vs spec §11 `document.parse_failed`.
- `app/parser/config.py:21,29-30,41` — `ocr_lang`, `max_file_bytes`, `max_pages_for_ocr`, `ParseLimits` unused; `RapidOCR()` constructed without args.
- `app/normalizer/rules.py:54` — `_WS_RE` compiled unused; `collapse_whitespace` re-inlines regex.
- `app/processing/executor.py:19` — imports private `_silent` across module boundary.
- `app/processing/config.py:24` + `executor.py:96-97` — `ocr_warm` eagerly builds RapidOCR on every batch run even for text-only corpora.
- `app/processing/corpus.py:45-53` — `hash_paths` reads each file fully, then executor reads again → 2x I/O at scale.

## Test gaps
- No ≥256-doc run (would have surfaced the deadlock); no image-doc re-parse byte-identity test (would have surfaced `put_image` nondeterminism); `tests/test_sbert_embedder.py` uses `bge-small-en-v1.5`, product default is `bge-m3`.
