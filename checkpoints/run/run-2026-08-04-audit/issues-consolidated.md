# Consolidated Issues — run-2026-08-04-audit

Deduped from `reviews/architecture.md` + `reviews/quality.md`. Priority tiers for the Implementation Engineer.

## TIER 1 — MUST FIX (correctness bugs, reproduced)
1. **BLOCKING `app/processing/executor.py:124-141`** — self-deadlock: `_record()` calls `_flush()` while holding the non-reentrant `threading.Lock` (line 89); `_flush` re-acquires it (line 140). Any batch crossing the flush boundary hangs. Fix the lock handling (e.g. single `_locked_flush` path; do not hold the lock while flushing).
2. **BLOCKING `app/parser/loaders/loaders.py:97-98`** — `load()` dispatches `plaintext`/`txt` to `self._plain`, which does not exist. Wire to the real text loader (`_text`, line 235) or rename. Add a `.txt` parser test.
3. **MAJOR `app/parser/storage.py:65-71`** — `put_image` key derived from live glob count → nondeterministic keys, orphaned files, violates idempotency (ADR #8). Key by content hash (stable) instead of run-history index.
4. **MAJOR `app/processing/corpus.py:26,65-66`** — `_hash_file` defined twice; streaming/chunked version is dead shadowed code; active version `read_bytes()` (whole file in RAM). Make streaming the active path.

## TIER 2 — SHOULD FIX (scale/data integrity)
5. **MAJOR `app/processing/executor.py:66-70` + `app/parser/extraction.py:50`** — each doc fully read + sha256'd twice; the hash already exists in `DocRef.sha256`. Pass it through so `extract` doesn't re-hash/re-read the full file.
6. **MAJOR `app/parser/config.py:29-30,41-44`** — `max_file_bytes`/`max_pages_for_ocr` declared but never enforced; `ParseLimits` dead. Either enforce them or remove the misleading knobs and record the decision.
7. **MAJOR `app/processing/corpus.py:56-62,89-90`** — files that fail to hash (`sha256 == ""`) are silently counted as skipped, never reported/retried. Surface them as failures.
8. **MAJOR `app/parser/storage.py:53-63`** — `put_dom`/`put_normalized` single-slot overwrites contradict ADR #8 (versioned outputs) + spec §10. Minimum: stop destroying prior versions; ideally honor `dom/<doc_id>/dom-v{version}`. This also removes the 100%-identical duplicate pair (quality reviewer's consolidation item).

## TIER 3 — CHEAP WINS (dead code / naming / docs)
9. `app/parser/dom/models.py:22-34` — `BlockKind(str)` non-enum, never referenced → make `str, Enum` or delete.
10. `app/parser/storage.py:82-83` — `to_json_bytes()` no callers → remove.
11. `app/normalizer/rules.py:54` — `_WS_RE` unused; `collapse_whitespace` re-inlines regex → use the compiled pattern.
12. `app/parser/detection.py:32-53` vs `loaders.py:29-44` — `_MIME` duplicated → single source.
13. `app/processing/executor.py:115-122` — backoff on deterministic non-transient failures → don't retry unsupported/unresolved.
14. `app/parser/events.py:3-5` + `extraction.py:55,61` — align emitted event names with spec §11 (`document.parse_failed`) and fix the stale docstring; `ParseOutcome` "empty" never produced.
15. `app/processing/executor.py:19` — imports private `_silent` across module boundary → public `silent_sink()` factory.
16. `app/parser/ocr.py:78-97` — `batch_ocr_bytes` mislabeled (one-at-a-time). Either fix docs or actually batch; and note it is never invoked (scale-batch spec overstates).

## TIER 4 — DEFER OR RECORD (design refactors; mark as new issues if deferred)
- `app/parser/loaders/loaders.py:96-119` — format dispatch is if/elif, not registry (spec §8 / universal §11). Refactor OR record as a tracked issue for a future run.
- `app/parser/loaders/loaders.py:131-217` — PDF loader has no OCR fallback for scanned pages (ADR #4 for images only). Record as a tracked issue if deferred.
- `app/processing/config.py:24` — `ocr_warm` eagerly builds RapidOCR even for text-only corpora. Wire or record.
- `app/processing/corpus.py:45-53` — 2x read per doc at scale. Overlaps Tier 2 #5; resolve there if possible.

## TESTS TO ADD (close the gaps that hid the bugs)
- `.txt` / plaintext loader test (would have caught #2).
- Batch run crossing the flush boundary (≥256 docs) (would have caught #1).
- Image-bearing doc re-parse → identical DOM bytes / identical storage keys (would have caught #3, #8).

**Constraint:** keep the modular-monolith boundaries intact; no new features; `.venv/Scripts/python.exe -m pytest tests/ -q` stays green; append-only memory.
