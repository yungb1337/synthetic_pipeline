# Fix Loop — Round 1, run-2026-08-19-page-centric

Both Gate 5 (architecture) and Gate 6 (quality) returned **FAIL**. This consolidates the
overlapping issue lists into one ordered work set for the implementation-engineer. After
fixes: run `pytest` GREEN, then a CLEAN corpus run (fresh `--out` dir) proving zero
`std::bad_alloc` + zero silent page loss, then re-run both reviewers.

Severity note: where Gate 5 and Quality disagree on severity, the WORSE (MAJOR) wins.

---

## A. SILENT-LOSS HOLES (highest priority — these break the run's central invariant)

### A1 — DocumentValidator must require `content_present` (Gate5 MINOR-7 == Quality M1, escalated to MAJOR)
- `app/parser/assembler.py:48-51` `assembled_page_set` must be
  `{r.page_index for r in results if r.status in (OK, PARTIAL) and r.content_present}`.
- A `PARTIAL` page with zero recovered content must NOT count as assembled → it falls into
  `missing`/`failed` → dead-letters. This is the literal §3.13 formula; the current code
  drops the `content_present` clause and lets an empty docling stub page report `parsed`.
- Fix `classify`/validate accordingly; ensure `extraction.py` only emits `document.parsed.v1`
  when `actual_pages == expected_pages` AND all assembled pages have content.

### A2 — Corrupt/unreadable PDF must NOT report `parsed` with 0 pages (Quality M2, MAJOR)
- `app/parser/source.py:82-84` `SourceScan.scan` swallows ALL fitz-open exceptions into
  `page_count=0`/`expected_page_set=[]`, which then yields an empty-but-`parsed` doc
  ("silent loss of everything").
- Fix: do not swallow a fitz open failure into a 0-page success. Surface it as
  `failed`/`unsupported` (raise or set a status the Extractor turns into a non-`parsed`
  outcome). A valid 0-page document is essentially impossible for PDFs; treat 0 expected
  pages as an error, not success.

### A3 — Retry must NOT downgrade `docling` → `native` (Gate5 MAJOR-3 == Quality M3)
- `app/parser/assembler.py:194-229` `_retry_pages`: bands other than enrichment/image/simple
  fall through to `NativePdfEngine` (line 219-220). A FAILED docling page is retried by the
  native engine, silently changing routing and never re-entering the heavy pool.
- Fix: route a `docling` page back through `HeavyDoclingEngine` (heavy pool) — re-enqueue as
  new `PageWorkItem`s (attempt+1) via the Scheduler, preserving `item.route`. Do not fall
  through to native except as an explicit, documented degrade guarded by config.

---

## B. CORE-FIX ENGINE UNVERIFIED (MAJOR)

### B1 — Add `HeavyDoclingEngine.process` unit test (Gate5 MAJOR-2 == Quality M4, MAJOR)
- `tests/test_page_centric.py` only fakes `docling_guard_status`; there is NO
  `HeavyDoclingEngine.process` test. Acceptance (architecture §10.2, T17) requires it.
- Add a guarded test (`importorskip("docling")` or monkeypatch `convert_path`):
  - `convert_path` → `ConversionResult(status=FAILURE)` ⇒ `PageResult.status == FAILED`,
    `errors` non-empty.
  - `convert_path` → `ConversionResult` with an empty/stub page (no blocks/tables/images)
    ⇒ `status` in `(PARTIAL, FAILED)` and `content_present is False`.
- Also add a test for the `_run_heavy` worker wrapper `convert_path` return-None → FAILED path.

---

## C. RESOURCE GOVERNOR / ENGINE LIFECYCLE (MAJOR — perf + RAM correctness)

### C1 — Do not load Docling engine in the orchestrator process (Gate5 MAJOR-4 + Quality Mo2)
- `scheduler.py:241` `Scheduler.__init__` unconditionally calls `measure_footprint`, which
  builds+warms the Docling engine in the orchestrator even for native-only / non-PDF runs
  (multi-hundred-MB load + seconds, per `Extractor` construction, even per-file in `cli.py`).
- Also `measure_footprint` (`scheduler.py:106-158`) measures `F` in the orchestrator, not the
  heavy worker (§3.12/§5 prescribe worker `initializer` + `multiprocessing.Value`).
- Fix:
  (a) Gate the F probe behind "docling will actually be used" — skip when no docling route is
      present, or when `heavy_concurrency` is explicitly supplied without docling in the mix.
  (b) When docling is used on the **ProcessPool** path, perform the F probe inside
      `_heavy_initializer` (or a dedicated probe worker) and publish to a
      `multiprocessing.Value` the governor reads. Do NOT build the engine in the orchestrator
      on the ProcessPool path. (The single-doc `prefer_in_process_heavy=True` path may still
      warm locally and reuse.)

### C2 — Wire `periodic_recheck` (Gate5 MAJOR-5)
- `scheduler.py:205-220` `ResourceGovernor.periodic_recheck` (downward-only re-derivation)
  has no caller. Invoke it every N completed documents in `Scheduler.run_plan` and resize the
  heavy pool downward (never spawn mid-flight upward surges).

### C3 — Governor math double-count (Quality Mi3)
- `scheduler.py:155`: `self.measured_f = max(peak, 0) + (rss - base)` double-counts the
  engine delta. Should be `max(peak, rss - base)`.

### C4 — Docling heap-reclaim options (Gate5 MINOR-6)
- `docling_loader._make_pipeline_options` (`docling_loader.py:162-194`): set
  `release_native_memory_every_n_pages=1` (and `doc_batch_concurrency=1`,
  `page_batch_concurrency=1` if available) defensively (try/except) on the heavy path, per §5.

### C5 — Wire `docling_guard()` startup drift-guard (Gate5 MAJOR-1)
- `docling_loader.py:1023-1051` `docling_guard()` exists but is never called by
  `engine_available()`/`get_engine()`. Call it (cached) at the top of `engine_available()`;
  return `False` on drift so a docling install with an incompatible
  `status`/`ErrorItem.page_no`/`page_range` API degrades gracefully to native at availability
  time instead of failing per-page.

---

## D. RESUME FEATURE (MAJOR — advertised objective not delivered)

### D1 — Wire genuine page-level resume (Gate5 MINOR-10 == Quality M5, escalated to MAJOR)
- `Planner.plan(resume=False)` default; no production caller passes True
  (`extraction.py:142`, `executor.py:83-87`), and `plan()` unconditionally rewrites the
  ledger with all pages `pending` (`planner.py:140`), so even the artifact isn't resumed.
- Fix: implement real resume — `plan()` reads the existing ledger and only re-plans
  non-OK pages (skip `OK`, reschedule `FAILED`/`DEAD` with attempt+1). Enable `resume=True`
  in the batch executor path (`executor.py`) so re-runs don't reparse done pages. Keep
  `Extractor.extract` semantics intact (single call still works; resume is opt-in for the
  batch path). If a full resume is too risky to wire into the hot path, at minimum stop
  clobbering the ledger and plumb `resume` through, and document the limitation — but the
  run objective explicitly requires per-page resume, so prefer implementing it.

---

## E. RETRY POLICY FROM CONFIG (MODERATE)

### E1 — `max_retries` hardcoded (Quality Mo1)
- `assembler.py:131` hardcodes literal `2`; `Extractor.extract` never passes it;
  `ParserConfig` has no `page_retries` field. Add `page_retries` to `ParserConfig`
  (`app/parser/config.py`) and thread it through `Extractor` → `Assembler`.

---

## F. BEHAVIOR REGRESSION (MODERATE)

### F1 — Heading classification per-page vs document-wide median (Quality Mo3)
- `engines/native_pdf.py:99-104` uses per-page median font size; legacy `Loaders._pdf` used
  document-wide median. Heading kind feeds downstream chunking. Add a regression test asserting
  heading *kinds* match the legacy output on a representative native PDF; if it diverges,
  revert to document-wide median for parity (or document the intentional per-page choice).

---

## G. CLEANUPS (MINOR — fix alongside)

- **G1** (Quality Mi1) Remove dead code: `source.py:104 read_source`, `source.py:117 is_image_slug`,
  `scheduler.py:18` unused `FIRST_COMPLETED`/`wait` imports, `source.py:16` unused `asdict`.
- **G2** (Quality Mi2) Hoist duplicated `_IMAGE_SLUGS` (`source.py:42`, `planner.py:25`) to one
  shared constant.
- **G3** (Quality Mi4) `Ledger.update_page` (`storage_pages.py:84`): accumulate attempts
  (`attempt + prev`) instead of `max(prev, attempt)`.
- **G4** (Gate5 MINOR-9) Only `DocumentBuilder.build` + `put_dom`/`put_raw` on success; skip
  DOM write for failed/dead docs (downstream already treats `po.ok==False` as failed, so this
  is artifact hygiene).
- **G5** (Gate5 MINOR-8) Either add explicit bounded-submission backpressure (submit in chunks
  of `max_workers`) or document the pool-size bound as sufficient. (Acceptable to document.)

---

## ACCEPTANCE FOR THIS FIX LOOP
1. `pytest tests/ -q` → GREEN (0 failures). The new `HeavyDoclingEngine` test (B1) + resume
   (D1) + heading regression (F1) must pass.
2. CLEAN corpus run: `python -m app.processing.cli --in "C:/Users/Asus/Downloads/test_cases"
   --out <FRESH_DIR> --concurrency 4` → ZERO `std::bad_alloc`/OOM; ZERO silent page loss
   (every doc reports actual==expected; no doc marked `parsed` with missing pages); all 15
   docs parse; DOM at `dom/<doc_id>/dom-v*.docJSON`.
3. Both reviewers (Gate 5 + Gate 6) re-run and return `VERDICT: PASS`.
4. Write `checkpoints/run/run-2026-08-19-page-centric/engineer-report.md` recording: the
   `VERDICT: IMPLEMENTED` line, pytest counts, corpus per-doc actual/expected + bad_alloc
   count, and a per-issue "fixed" map referencing A1..G5.
