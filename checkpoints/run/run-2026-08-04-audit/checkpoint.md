# Checkpoint — run-2026-08-04-audit

**Run id:** `run-2026-08-04-audit` — the org's FIRST run
**Date:** 2026-08-04
**Orchestrated by:** Project Orchestrator (background `audit` workflow)
**Status:** **COMPLETE** — both reviewers **PASS** after 2 fix rounds; suite **31 green**.

## Objective
Audit the existing modules — `app/parser`, `app/normalizer`, `app/processing`, `app/embedding` — for architecture alignment (specs in `docs/` + ADRs in `project_memory/architecture_decisions.md`), code duplication (`scripts/check_similarity.py`), and quality/performance at millions-of-docs scale. Constraints: no new features; modular-monolith boundaries intact; append-only memory. Run brief: `project_memory/active_objective.md`.

## Definition of done — met
- Architecture Reviewer: **VERDICT: PASS** (was FAIL at round 0)
- Quality & Performance Reviewer: **VERDICT: PASS** (was FAIL at round 0)
- `.venv/Scripts/python.exe -m pytest tests/ -q` → **31 passed, 0 failed** (was 27 → 30 after round 1 → 31 after round 2)
- This checkpoint written.

## What each gate produced
- **Gate 1 — Architecture Review** → `reviews/architecture.md`. Found 1 BLOCKING (batch-layer self-deadlock: `_record()` calls `_flush()` while holding the non-reentrant `threading.Lock`), 6 MAJOR (nondeterministic `put_image` keys, single-slot DOM overwrites, no PDF OCR fallback, shadowed `_hash_file`, unwired `batch_ocr_bytes`, silent failed-hash skip, no loader registry), 5 MINOR, 3 test gaps. Verdict FAIL.
- **Gate 2 — Quality & Performance Review** → `reviews/quality.md`. Found 2 BLOCKING (the same self-deadlock, reproduced with `_flush_every=2`; missing `_plain` `.txt` loader → `AttributeError` on every `.txt` file), 3 MAJOR, 8 MINOR, 2 test gaps. Verdict FAIL.
- **Issue consolidation** → `issues-consolidated.md`. Deduped into TIER 1 (4 must-fix), TIER 2 (4 should-fix), TIER 3 (8 cheap wins), TIER 4 (4 defer-or-record) + tests to add.
- **Implementation / fix rounds** → `engineer-report.md` (round 1 + round 2).
- **Re-gates** → both reviewers PASS after round 1 and again after round 2.

## Fix rounds
- **Round 1** — fixed all TIER 1 + TIER 2: self-deadlock (compute `needs_flush` under lock, release, then flush), `.txt`/`plaintext` → `_text` loader, content-hash image keys `images/{doc_id}/{sha256}.{ext}` write-if-absent, streaming 1 MiB `_hash_file` as the single active path, single-pass hash via `DocRef.sha256` (no double re-read), enforced `max_file_bytes`, failed-hash surfaced as failures, versioned DOM keys. Fixed all TIER 3: `BlockKind` deleted, `to_json_bytes()` removed, `_WS_RE` rewritten (later regressed), `_MIME` deduped → `app/parser/mime.py`, deterministic failures not retried (`DocResult.retriable`), events aligned to `document.parse_failed`, public `silent_sink()`, `batch_ocr_bytes` docstring corrected. Added 3 tests (`.txt` loader, image re-parse determinism, 300-doc flush boundary). Suite 27 → 30.
- **Round 2** — fixed the round-1 regression `_WS_RE` non-idempotency (single-match-per-run `(?:[ \t]*\n)+[ \t]*|[ \t]{2,}`; restores the documented "second pass is a no-op" contract the batch incremental re-run depends on) + regression test; `needs_flush` UnboundLocalError on `skipped`; residual MIME string literals consolidated to `mime.py`; storage docstring/key drift reconciled; **manifest dirty-flag** added (skip full rewrite when no new shas since last write — O(n²) mitigation). Suite 30 → 31.

## Key hardening delivered
- Batch-layer self-deadlock fixed (release lock before flush).
- `.txt`/plaintext loader wired + regression test.
- Content-addressed image keys, write-if-absent (ADR #8).
- Versioned DOM storage `dom/{doc_id}/dom-v{version}.docJSON` / `norm-v{version}.docJSON`; prior versions retained.
- Idempotent normalizer `_WS_RE` (second pass is a no-op).
- Manifest dirty-flag — O(n²) mitigation; full rewrite at millions-scale still deferred.

## Deferred / tracked issues (6 → promoted to `project_memory/questions.md`)
1. Manifest full-rewrite O(n²) at millions-scale — deeper fix (incremental/segment manifest or append-only journal) is a design change.
2. Failed/empty-sha docs re-parsed every run — no dead-letter/backoff; design decision needed.
3. Loader registry (if/elif → registry; spec §8 / universal §11).
4. PDF OCR fallback for scanned pages (ADR #4 covers standalone image files only).
5. `ocr_warm` eager RapidOCR build (wire it or gate it).
6. `tests/test_sbert_embedder.py` uses `bge-small-en-v1.5` vs product `bge-m3`; also non-hermetic (network download when uncached).

Full reasoning in `deferred-issues.md`; promoted one-per-line into [[questions]].

## Process notes (the org's first run)
- Reviewers ran **read-only** (as designed): the Orchestrator had to transcribe their verdicts into `reviews/*.md` artifacts. It worked, but adds a transcription hop and risks losing reviewer nuance. **Recommendation: grant reviewers scoped Write to `reviews/` next run** so they write their own verdict files.
- Storage-layout fix rippled outside declared scope: `.claude/skills/run-synthetic-data-factory/driver.py:102` glob broke (fixed to `rglob("dom-v*.docJSON")`), and `SKILL.md` still documents the old layout — drift noted, flagged for a docs pass. Takeaway: inventory every glob/layout consumer when touching storage keys.
- Two fix rounds were needed because round 1 introduced a regression (`_WS_RE` idempotency); the round-2 re-review caught it. Takeaway: re-verify normalizer idempotency invariants on any regex/whitespace change.
- Suite went 27 → 30 → 31: 3 audit tests added in round 1, 1 regression test in round 2.

## Links
- Reviews: `reviews/architecture.md` · `reviews/quality.md`
- Consolidation: `issues-consolidated.md`
- Fixes: `engineer-report.md` (rounds 1 + 2)
- Deferred: `deferred-issues.md`
- Promoted: `project_memory/questions.md` → "Tracked issues from run-2026-08-04-audit"
- ADR: `project_memory/architecture_decisions.md` → storage layout entry (versioned DOM, content-addressed immutables)
- Status: `project_memory/module_status.md` → audit status line
