# Quality & Performance Review — run-2026-08-04-audit

**Recorded by:** Project Orchestrator, from the Quality & Performance Reviewer's report (reviewer is read-only).

## Verdict
**VERDICT: FAIL**

## Evidence collected
- `python scripts/check_similarity.py` → 33 files, 71 units, **1 pair ≥ 0.40**: `put_dom` vs `put_normalized` (`storage.py:53-63`), Jaccard 1.00 (9/9 trigrams).
- `.venv/Scripts/python.exe -m pytest tests/ -q` → **27 passed**. Meaningful, deterministic tests (content/structure/idempotence) — but with coverage gaps.

## Blocking
- **BLOCKING `app/processing/executor.py:124-141`** — self-deadlock: `_record` calls `_flush` while holding the non-reentrant `self._lock`; `_flush` re-acquires it. Reproduced with `_flush_every=2`. Any batch crossing the flush boundary hangs.
- **BLOCKING `app/parser/loaders/loaders.py:97-98`** — `load()` dispatches `plaintext`/`txt` to `self._plain`, which does not exist (reproduced `AttributeError`); the intended `_text` at line 235 is never dispatched. Every `.txt` file crashes the parser.

## Major
- `app/processing/corpus.py:26,65-66` — `_hash_file` defined twice; streaming/chunked version shadowed dead code; active version `read_bytes()` (whole file in RAM).
- `app/parser/config.py:29-30,41-44` — `max_file_bytes` / `max_pages_for_ocr` declared but never enforced; `ParseLimits` dead.
- `app/processing/executor.py:66-70` + `app/parser/extraction.py:50` — each doc fully read + sha256'd twice (once in `hash_paths`, again inside `extract`), though hash already in `DocRef.sha256`.

## Minor
- `storage.py:53-63` — `put_dom`/`put_normalized` identical modulo filename suffix; consolidate.
- `app/parser/dom/models.py:22-34` — `BlockKind(str)` is a non-enum `str` subclass, never referenced.
- `app/parser/storage.py:82-83` — `to_json_bytes()` no callers.
- `app/parser/storage.py:68` — `put_image` key from live glob count, contradicts documented content-addressed layout.
- `app/parser/detection.py:32-53` vs `loaders.py:29-44` — `_MIME` mapping duplicated across modules.
- `app/processing/executor.py:115-122` — retries apply backoff to deterministic non-transient failures (unsupported/unresolved).
- `app/parser/events.py:3-5` — docstring names `document.parse_failed`; code emits `document.unresolved`/`document.failed` (`extraction.py:55,61`); `ParseOutcome` "empty" status never produced.
- `tests/test_sbert_embedder.py:21-24` — module-scope fixture loads real model; network download when uncached (non-hermetic).
- `app/parser/ocr.py:78-97` — `batch_ocr_bytes` documented as batched but processes one image at a time.
- `app/parser/loaders/loaders.py:316,328-329,391` — docx namespace expressed three ways in one method.
- `app/parser/dom/builder.py:133` — provenance field `oct_level` opaque naming.

## Test gaps
- No plaintext/txt loader test (why the missing `_plain` shipped undetected); no batch run past flush boundary; `test_sbert_embedder` non-hermetic.
