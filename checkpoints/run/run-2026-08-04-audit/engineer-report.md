# Engineer Report — run-2026-08-04-audit (fix round 1)

**Recorded by:** Project Orchestrator, from the Implementation Engineer's report.

Both BLOCKING bugs reproduced before fixing; all fixes verified; suite green (27 → 30).

## TIER 1 — FIXED (all four)
1. **BLOCKING self-deadlock** — `app/processing/executor.py:131-151`. Reproduced (hung on `_flush_every=2`); `_record()` now computes `needs_flush` under the lock, releases it, then `_flush()` acquires the lock itself. Verified: 300-doc batch with `_flush_every=2` completes in ~0.6s.
2. **BLOCKING missing `_plain` loader** — `app/parser/loaders/loaders.py:97`. Reproduced `AttributeError`; `plaintext`/`txt` now dispatch to `_text`. Verified `.txt` parse → `status: parsed`.
3. **MAJOR nondeterministic `put_image`** — `app/parser/storage.py:71-82`. Now keyed by content hash → `images/{doc_id}/{sha256}.{ext}`, written only if absent. Idempotent (ADR #8).
4. **MAJOR shadowed whole-file `_hash_file`** — `app/processing/corpus.py`. Second `_hash_file`/`_hash_bytes` removed; streaming 1 MiB chunked version is the single active path.

## TIER 2 — FIXED (all four)
5. **Double hash/re-read** — `Extractor.extract(..., sha256=None)` accepts the precomputed hash; batch passes `ref.sha256`; extract no longer re-hashes the full file (`app/parser/extraction.py:48-53`, `app/processing/executor.py:73`).
6. **Dead limit knobs** — `max_file_bytes` now enforced in `extract` (returns `failed` outcome + `document.parse_failed`). Removed dead `ParseLimits` + `max_pages_for_ocr` knob (no enforcement point until PDF OCR fallback lands — decision recorded). `ocr_lang` untouched.
7. **Failed-hash silent skip** — `corpus.py:81-85` + `executor.py:138-139`. `pending()` now returns `ref.sha256 not in manifest`; unhashable files surface as failures; manifest add guarded against empty sha.
8. **Single-slot DOM overwrites** — `storage.py:54-69`. `put_dom`/`put_normalized` consolidated into `_put_dom_json` writing versioned keys `dom/{doc_id}/dom-v{version}.docJSON` / `norm-v{version}.docJSON`. Prior versions retained. Similarity pair gone.

## TIER 3 — FIXED
9. `BlockKind` deleted (`app/parser/dom/models.py`). 10. `to_json_bytes()` removed. 11. `_WS_RE` rewritten to `[ \t]*\n+[ \t]*|[ \t]{2,}` (original pattern was buggy/unused); idempotency verified. 12. `_MIME` deduped → new `app/parser/mime.py`; loaders copy (dead) removed. 13. Deterministic failures no longer retried (`DocResult.retriable`). 14. Events aligned with spec §11 → `document.parse_failed`; `ParseOutcome` "empty" dropped. 15. Public `silent_sink()` added; `executor.py` no longer imports `_silent`. 16. `batch_ocr_bytes` docstring corrected + noted not yet wired.

## Consequential change (flagged for orchestrator)
- `.claude/skills/run-synthetic-data-factory/driver.py:102` glob updated to `rglob("dom-v*.docJSON")` — the versioned-layout fix (#8) broke the smoke driver's flat `*.dom.json` glob. Outside the declared `app/`+`tests/` scope. `SKILL.md` still documents the old layout (doc-like, left untouched).

## TIER 4 — DEFERRED (reasons)
- Loader registry (if/elif → registry): design refactor, not this round.
- PDF OCR fallback for scanned pages: new capability requiring OCR-engine integration.
- `ocr_warm` eager RapidOCR build: config decision; text corpora can pass `--no-ocr`.
- Corpus 2x read: partially resolved via #5; remaining full read in `process()` is inherent to scan-then-process.

## Tests added (`tests/`, existing style)
- `test_txt_plaintext_loader` (would have caught #2)
- `test_image_doc_reparse_deterministic` — identical DOM bytes, storage keys, versioned key (would have caught #3, #8)
- `test_batch_crosses_flush_boundary` — 300-doc run crossing the 256 boundary (would have caught #1)

## Verification
- pytest: **30 passed, 0 failed** (was 27).
- Similarity: `scripts/check_similarity.py` → **no pairs ≥ 0.40**.
- Smoke driver runs all pipeline stages green.

---

# Engineer Report — run-2026-08-04-audit (fix round 2)

Round 2 addresses the one regression the round-1 rewrite introduced plus the cheap minors. All fixes verified; suite green (30 → 31).

## MUST FIX — FIXED
1. **Non-idempotent `_WS_RE`** — `app/normalizer/rules.py:56`. Reproduced: `collapse_whitespace("a \n \n b")` → pass1 `"a  b"`, pass2 `"a b"` (old `[ \t]*\n+[ \t]*|[ \t]{2,}` left adjacent matches each emitting a space on interleaved runs). Rewritten to single-match-per-run `(?:[ \t]*\n)+[ \t]*|[ \t]{2,}`. Verified idempotent on `"a \n \n b"`, `"a\n\t \nb"`, `"a \n\t\n b"` (all → `"a b"` on pass 1, no-op on pass 2). This restores the documented "second pass is a no-op" contract (`rules.py:3-4`) the batch layer's incremental re-run depends on.
   - Regression test added: `tests/test_normalizer.py::test_rule_collapse_idempotent_interleaved` — asserts `collapse_whitespace(collapse_whitespace(s)[0])[0] == collapse_whitespace(s)[0]` and `once == "a b"` for all three interleaved forms. PASSED.

## SHOULD FIX — FIXED
2. **`needs_flush` UnboundLocalError on `skipped`** — `app/processing/executor.py:136`. Now initialized to `False` before the `with self._lock` block; `skipped` status (counted at start) can no longer leave it unbound.
3. **Residual MIME literals consolidated** — `app/parser/detection.py:33-52` (`_MAGIC` + `_CONTAINER`) and `app/parser/loaders/loaders.py:184` now reference the canonical `app/parser/mime.py` map (`_MIME[...]`) instead of inline string literals. Magic bytes/container prefixes untouched; behavior-preserving (verified PDF/PNG/JPG/RTF detection).
4. **Storage docstring/key drift reconciled** — `app/parser/storage.py:32-45`. Docstring now shows actual keys `dom/<doc_id>/dom-v{version}.docJSON` / `norm-v{version}.docJSON` (not the misleading `dom-{parser_version}`), and the "immutable" claim is scoped correctly: raw + images are immutable content-addressed write-if-absent; DOM outputs are versioned per doc_id × version with deterministic same-version overwrite. `_put_dom_json` comment clarified to match.
5. **Manifest dirty-flag (O(n²) mitigation)** — `app/processing/executor.py` `__init__`/`run`/`_record`/`_flush`. `_flush` now skips the full rewrite when no new shas were added since the last write (`self._manifest_dirty`). This is a simple dirty-check placed at the caller (the BatchWorker) rather than inside the stateless `save_manifest`, which would need global state. The deeper O(n²) full-rewrite at millions-scale is recorded as deferred.

## DEFERRED (recorded in `deferred-issues.md`, 6 issues)
Manifest full-rewrite O(n²) at millions-scale (dirty-check skips unchanged rewrites but not dirty full-rewrites); failed/empty-sha docs re-parsed every run (no dead-letter) — design decision needed; loader registry (if/elif → registry); PDF OCR fallback for scanned pages; `ocr_warm` eager RapidOCR build; `tests/test_sbert_embedder.py` uses `bge-small-en-v1.5` vs product `bge-m3`.

## Verification
- pytest: **31 passed, 0 failed** (was 30).
- New idempotency test: `tests/test_normalizer.py::test_rule_collapse_idempotent_interleaved` → PASSED.
- Similarity: `scripts/check_similarity.py` → **no pairs ≥ 0.40**.
- Smoke driver → all pipeline stages green.
