# Parser Production-Readiness Failure Points

**Scope:** `app/parser/` + `app/processing/` (the document → canonical DOM pipeline).
**Goal:** make the parser fast, production-grade, fault-tolerant, with high failure
recovery and *no silent failures*.
**Method:** manual source audit + a runnable verification harness
([`scripts/verify_failure_points.py`](scripts/verify_failure_points.py)) that
reproduces each defect on a synthetic PDF so you can confirm them yourself before
any implementation.

> Every finding below was executed against the code and, where marked **[REPRODUCED]**,
> the harness reproduced the failure on this machine. Line numbers are 1-indexed
> and were read directly. Nothing in the repo was modified by the harness.

Run it:

```bash
.venv/Scripts/python.exe scripts/verify_failure_points.py          # all checks
.venv/Scripts/python.exe scripts/verify_failure_points.py F-01 F-02  # a subset
```

---

## Summary

| ID | Sev | Title | One-line |
|----|-----|-------|----------|
| [F-01](#f-01-blank-page-destroys-the-document) | **P0** | Blank page destroys the document | One content-free page → 3 good pages are discarded, no DOM written |
| [F-02](#f-02-resume-destroys-completed-work) | **P0** | Resume destroys completed work | `resume=True` (always on in batch) dead-letters every already-OK page |
| [F-03](#f-03-torn-ledger-erases-the-audit-trail) | P1 | Torn ledger erases the audit trail | Non-atomic write + swallowed load error silently wipes all page state |
| [F-05](#f-05-native-path-is-quadratic) | P1 | Native path is O(n²) in page count | Each page re-scans all N pages to recompute a median font size |
| [F-06](#f-06-source-pdf-reopened-per-page) | P1 | Source PDF re-opened per page | `fitz.open()` called N+1 times for an N-page document |
| [F-07](#f-07-page_exists-as-success-proxy) | P1 | `page_exists` used as "succeeded" proxy | A persisted FAILED page is treated as done by the safety net |
| [F-08](#f-08-no-logging-no-timeouts-no-atomic-writes) | P1 | No logging / no timeouts / no atomic writes | 0 logging, 0 timeouts, 0 `os.replace`/`fsync` anywhere in `app/` |
| [F-09](#f-09-no-brokenprocesspool-recovery) | P1 | No `BrokenProcessPool` recovery | One OOM-killed worker kills the whole heavy pool for the rest of the run |
| [F-10](#f-10-batch-mode-failures-are-invisible) | P1 | Batch-mode failures are invisible | Events routed to a no-op sink; no log; no queryable failure record |
| [F-04](#f-04-ledger-rewrite-is-quadratic-in-page-count) | P2 | Ledger rewrite is O(n²) per page | Whole `plan.json` re-serialized on every per-page update |
| [F-11](#f-11-docling-config-is-silently-ignored) | P2 | Docling config silently ignored | `default_config()` used instead of the passed `ParserConfig` on the heavy path |

---

## P0 — silent data loss / whole-document destruction

### F-01: Blank page destroys the document
**Location:** `app/parser/assembler.py:56-60`, `183`, `217-220`
**Severity:** P0

The hard gate `DocumentValidator.assembled_page_set` counts a page as "assembled"
**only if** its `PageResult` has `content_present == True`:

```python
ok = {r.page_index for r in results
      if r.status == PageStatus.OK and r.content_present}
```

`content_present` is `True` whenever a page has *any* block/table/image. A
**legitimately blank page** (a cover, divider, or figure-only page with no text
layer) has `status=OK` but `content_present=False` → it is counted as **missing**.
Because `actual_pages (3) != expected_pages (4)`, the document is reported
`partial`/`failed` and **`is_success` is False**, so the assembler writes **no DOM
and no raw artifact at all** (`assembler.py:217-220`). The 3 good pages are
discarded.

**Reproduction (F-01 in the harness):**
```
blank-page doc    : status='partial'  pages=3/4  dom_key=None
control (no blank): status='parsed'   pages=4/4  dom_key='dom/d-.../dom-v0.1.0.docJSON'
```
A 4-page PDF with one blank page yields `dom_key=None` — the entire document is
lost. **[REPRODUCED]**

**Blast radius:** one page → whole document. In `app/processing/executor.py:115-117`
`po.ok` is False → `DocResult(status="failed", retriable=False)`; a blank page is
not a transient error, so the document is **permanently dropped** with no retry.

**Fix sketch:** distinguish "empty because blank" from "empty because failed". A
page that was processed without error and produced provenance (page existed in the
source `expected_page_set`, engine returned OK) must count as assembled even when
it carries no text — it is a *valid* blank, not a *missing* page. Empty pages
should be represented in the DOM (a typed `blank`/page node) so the page set is
complete and the reading order stays intact.

---

### F-02: Resume destroys completed work
**Location:** `app/parser/planner.py:137-152`, `app/parser/extraction.py:159`,
`app/processing/executor.py:114`
**Severity:** P0

`Planner.plan` builds `work_items` by iterating `expected_page_set` and **skipping
pages whose prior ledger entry is `ok` AND present on disk**. It then unconditionally
`write_plan(...)` with **every** expected page reset to `pending` (`planner.py:152`,
`to_ledger()` lines 44-47). `Extractor.extract` ignores the returned `work_items`
entirely — `run_plan(plan)` iterates `plan.work_items` (`scheduler.py:306`) and
persists whatever it produces; `extract` never reloads the prior OK pages. The
assembler then folds only the (blank) `results` it was given; pages not in
`results` are classified `missing` → `dead` → whole document fails.

Meanwhile `app/processing/executor.py:114` calls `extract(..., resume=True)`
**unconditionally** for every document in a batch. So re-running a batch (e.g.
after a partial crash, the entire point of the "done manifest") **re-scans the
already-OK pages, drops them from `work_items`, and then dead-letters the whole
document**.

**Reproduction (F-02 in the harness):**
```
run 1 (resume=False): status='parsed'  pages=4/4
                       ledger={'0':'ok',...,'3':'ok'}   4 page artifacts on disk
run 2 (resume=True ): status='failed'  pages=0/4  dom_key=None
                       ledger={'0':'pending',...,'3':'pending'}
                       assembly='dead' {dead_pages:[0,1,2,3], actual_pages:0}
```
4 valid page artifacts remain on disk but are excluded from `work_items`, never
loaded, then dead-lettered. **[REPRODUCED]**

**Fix sketch:** `extract()` must reconstruct `results` from the ledger's OK pages
(when `resume=True`) and pass the *merged* list to the assembler, instead of
silently using only the freshly-produced subset. The resume test
(`tests/test_page_centric.py:147`) asserts only `work_items`, never that the
document assembles — which is why this passed CI.

---

## P1 — documents fail/degrade without operators knowing; recovery broken

### F-03: Torn ledger erases the audit trail
**Location:** `app/parser/storage_pages.py:57-60`, `63-70`, `74-76`
**Severity:** P1

`write_plan` does an **in-place** `p.write_text(...)` (no temp-file + `os.replace`),
so a crash mid-write leaves a **truncated JSON** file. `load_plan` then does:

```python
try:
    return json.loads(p.read_text(...))
except Exception:
    return None          # silently swallowed
```

and `update_page`/`update_assembly` both do `plan = self.load_plan(doc_id); if plan is None: return`
(also silent). Consequence: one torn write → all prior page statuses and recorded
errors vanish, every subsequent update is a no-op, and **no exception is raised**.
In batch mode (events → `silent_sink`, see F-10) the ledger is the *only* failure
record.

**Reproduction (F-03 in the harness):** a truncated `plan.json` → `load_plan` returns
`{'pages': {}}` (all state gone), and `update_page`/`update_assembly` raise nothing.
**[REPRODUCED]**

**Fix sketch:** write to `plan.json.tmp` then `os.replace()` (atomic on POSIX +
Windows). On load, if the main file is corrupt, attempt a `.tmp` recovery and/or
fall back to returning the last known-good plan rather than `None`; surface a
corruption event instead of swallowing it.

---

### F-05: Native path is O(n²) in page count
**Location:** `app/parser/engines/native_pdf.py:162-171`
**Severity:** P1

`NativePdfEngine.extract_page` re-opens the PDF and **re-scans every page's text**
to compute the document-wide median font size — and it does this *once per page*:

```python
for pi in range(doc.page_count):                      # <- for EVERY page
    for blk in doc[pi].get_text("dict")...:           # re-read all N pages
        ...
body_med = sorted(sizes)[len(sizes)//2]
```

So a document costs O(N²) text parsing. **Reproduction (F-05):**
```
5  pages: 60 ms/page   40 pages: 112 ms/page   (1.86x growth; superlinear)
```
A 500-page native PDF is dominated by this, not by the actual extraction.
**[REPRODUCED]**

**Fix sketch:** compute the median once per document, cache it, and pass it into
the per-page helper (the helper already accepts `body_med`). The median is a
document-level property and must not be recomputed per page.

---

### F-06: Source PDF re-opened per page
**Location:** `app/parser/engines/native_pdf.py:146`
**Severity:** P1

`extract_page` calls `fitz.open(src_path)` for **every page**, even though the
whole document is processed page-by-page in one process. **Reproduction (F-06):**
a 20-page document → `fitz.open()` called **21 times** (once in `extract_page` per
page, plus the scan). **[REPRODUCED]** This compounds F-05 (each open re-parses the
structure) and inflates peak memory (N open document handles).

**Fix sketch:** open the document once per engine invocation, iterate pages on the
single handle, close in `finally`. (Docling's `convert_path` is per-page by design
to bound the C++ heap — that part is fine; native need not be.)

---

### F-07: `page_exists` used as a "succeeded" proxy
**Location:** `app/parser/extraction.py:262-268`, `app/parser/scheduler.py:363-369`,
`app/parser/storage_pages.py:46-47`
**Severity:** P1

The document-level safety net decides whether to mark a page FAILED like this:

```python
for p in plan.expected_page_set:
    if not self.page_store.page_exists(doc_id, p):   # file exists at all?
        self.ledger.update_page(doc_id, p, PageStatus.FAILED, ...)
```

`page_exists` returns True for *any* file at that path — including a **persisted
FAILED `PageResult`**. **Reproduction (F-07):** a `PageResult(status=FAILED)` is
written via `put_page`; `page_exists` returns `True` and `get_page().status ==
'failed'`. So `_fail_document` skips it and never records the failure on the
ledger. **[REPRODUCED]** A page that genuinely failed during execution can thus
leave the ledger showing `pending` (or whatever the scheduler wrote) while the
safety net assumes it succeeded.

**Fix sketch:** check *status* (`get_page(...) is not None and status != FAILED/DEAD`),
not mere file existence, when deciding what to mark. Persist `FAILED`/`DEAD`
explicitly in `_fail_document` rather than only on the success path.

---

### F-08: No logging / no timeouts / no atomic writes
**Location:** whole `app/` (static audit in the harness, F-08)
**Severity:** P1

The harness scanned **79** Python files in `app/`:
- **logging references: 0** — no `logging` subsystem exists; the only error
  channel is `print` (console sink) or a no-op (batch). Failures are not
  structured, not leveled, not persisted.
- **timeout references: 0** — `fut.result()` (`scheduler.py:354`), every third-party
  call (Docling convert, OCR, `fitz`), and every filesystem read have **no
  timeout**. A single hung Docling page blocks the run forever; a slow network
  model fetch never times out.
- **atomic writes (`os.replace`/`fsync`): 0** — every write site
  (`storage.py`, `storage_pages.py`, `chunking/store.py`, `source.py`) writes
  in-place. Torn files on crash (see F-03) are the direct result.
- **`except Exception:` / `except:` : 115** occurrences, **42 of which are
  immediately followed by `pass`** — silent swallowing, many around *persistence*.

**[REPRODUCED]** This is the foundational observability + durability gap behind
several other findings.

**Fix sketch:** (1) introduce a `logging`-based, structured logger with
correlation IDs threaded through page workers; (2) give every external/IO call a
timeout (future `result(timeout=...)`, Docling/OCR timeouts); (3) add one
atomic-write helper (`write_atomic(path, data)` = temp + `os.replace` +
optional `fsync`) and route all durable writes through it.

---

### F-09: No `BrokenProcessPool` recovery
**Location:** `app/parser/scheduler.py:279-293`, `352-372`
**Severity:** P1

The heavy (Docling) path runs in a `ProcessPoolExecutor`. When a worker dies
abnormally — the OOM killer reaping it, a `std::bad_alloc`/`SIGSEGV` in the C++
layout/segmentation heap, or a CUDA error — the executor transitions to the
**`BROKEN`** state. `BrokenProcessPoolExecutor` raises `BrokenProcessPoolError` on
*every* subsequent `submit()`/`result()`, and there is **no code anywhere that
catches or rebuilds it** (grep for `BrokenProcessPool` → 0 hits). Once one page
OOMs, the entire heavy pool is dead for the **rest of the process**, so a 1000-page
Docling job loses all remaining pages after a single worker death — exactly the
"high fault tolerance" scenario this project targets.

**Fix sketch:** wrap pool usage; on `BrokenProcessPoolError`, discard the broken
pool, spin up a fresh one, and re-submit the affected pages (they will be
re-classified and retried by the assembler). Treat a worker death as a *page-level*
fault, not a *run-level* one. Consider `max_tasks_per_child` to bound leaked
native memory per worker.

---

### F-10: Batch-mode failures are invisible
**Location:** `app/parser/events.py:22-29` (`_silent`), `app/processing/executor.py:84`
**Severity:** P1

`EventPublisher` has only two sinks: a console `print` (dev) and `_silent`, which
returns `None` — a total no-op. `ParseNormalizePipeline` wires the extractor to
`EventPublisher(sink=silent_sink())` (`executor.py:84`) "because batch pipelines
emit to a broker, not stdout" — **but no broker exists**. So in production batch
runs, `document.parse_failed`, per-page errors, and the "zero silent loss" proof
are emitted into the void. The only trace of a failure is a string in
`BatchReport.errors` (`f"{name}: {error}"`). Combined with F-08 (no logging) and
F-03 (torn ledger), a permanently-failed document at 100k scale is effectively
**undetectable and undiagnosable** without re-running it.

**Fix sketch:** implement a real sink (structured log file + optional broker)
behind the existing `EventPublisher` seam; never default batch to the no-op sink.
Emit a durable `document.parse_failed` with the full `expected/actual/missing/failed/dead`
breakdown so operators can answer "which documents are wrong and why" without
re-running.

---

## P2 — throughput / cost / hardening

### F-04: Ledger rewrite is O(n²) in page count
**Location:** `app/parser/storage_pages.py:72-90`
**Severity:** P2

`update_page` does a **read-modify-write of the entire `plan.json`** on every
single page result (load full plan → mutate one key → re-serialize the whole
thing). Cost grows with `pages²`. **Reproduction (F-04):**
```
50  pages: 11.6 ms/page   200: 12.8 ms/page   800: 17.1 ms/page   (plan.json 157 KiB)
```
That is ~13.6 s of pure JSON bookkeeping for an 800-page document, *on top of*
extraction. **[REPRODUCED]** At corpus scale this is a real throughput tax.

**Fix sketch:** store per-page state as individual small files
(`manifest/<doc>/pages/p<idx>.json`) written atomically, with a thin plan header
separate from per-page records. Or append-only, or a real embedded store. Avoid
rewriting the whole ledger per page.

---

### F-11: Docling config is silently ignored
**Location:** `app/parser/loaders/docling_loader.py:181-231` (`_make_pipeline_options`,
`_set_table_structure_mode`, `_set_ocr_options`, `_models_dir` all call
`default_config()`), `app/parser/engines/heavy_docling.py:32`, `app/parser/loaders/docling_loader.py:1159`
**Severity:** P2

The heavy Docling path `convert_path(path, page, models_dir)` takes **no
`ParserConfig`** — only `models_dir`. Every engine-tuning function reads
`default_config()` instead of the config the `Extractor` was constructed with. So
`docling_table_mode` ("FAST" vs "ACCURATE" — the D2 fix central to the
extraction-quality run), `docling_ocr`, `ocr_lang`, etc. are **silently ignored** on
the actual production path. They only "work" because `default_config()` happens to
default to the same values. Passing a non-default `ParserConfig` (e.g.
`docling_table_mode="ACCURATE"`) has **no effect** on the heavy engine.

**Fix sketch:** thread the `ParserConfig` (or the relevant subset) through
`HeavyDoclingEngine` → `convert_path` → `_make_pipeline_options` /
`_set_table_structure_mode` / `_set_ocr_options`, and assert in a test that a
non-default `docling_table_mode` changes engine behaviour.

---

## Root-cause clusters (fix the system, not the symptom)

1. **No durability layer.** F-03, F-08(atomic), F-04. One atomic-write helper +
   per-page state files replaces the whole-ledger rewrite and torn-write class of
   bugs.
2. **No observability layer.** F-08(logging/timeout), F-10, F-07. A logger +
   real event sink + per-page status (not existence) checks make failures visible
   and recoverable.
3. **Resume/state reconstruction is unfinished.** F-02, F-07. `extract()` must
   merge ledger-OK pages into `results` and decide status by *status*, not file
   presence.
4. **Per-document work is recomputed / re-opened.** F-05, F-06. Open once, compute
   document-level aggregates (median font, page geometry) once, pass down.
5. **Blank/empty ≠ failed is not modelled.** F-01. A page processed without error
   is assembled even with no text; represent it explicitly in the DOM.
6. **Process-pool failure is run-fatal.** F-09. Worker death must be a page-level
   retry, with pool rebuild.
7. **Config does not flow to engines.** F-11. Single source of truth for
   `ParserConfig` threads through every engine.

---

## Machine-checkable invariants to assert in production

1. `assembled_page_set == expected_page_set  OR  status != "parsed"`.
2. `status == "parsed"  ⇒  dom_key is not None` (no parsed doc without a DOM).
3. `content_present == False AND status == OK  ⇒  page is a valid blank` (not
   silently dropped).
4. `resume=True AND ledger.page_ok(p)  ⇒  p ∉ work_items AND p ∈ results` (OK pages
   are loaded, not re-parsed, and never dead-lettered).
5. Every persisted `PageResult` write is atomic (tmp + `os.replace`); `load_plan`
   never returns `None` for a file that exists (corruption is an explicit error).
6. `fitz.open` called exactly once per document in the native path.
7. Heavy pool in `BROKEN` state ⇒ rebuilt before next `submit`; no run-wide abort.
8. `docling_table_mode` (and other Docling opts) set on the config == value used by
   the engine (asserted by test).

---

## Suggested remediation order

| Wave | Closes | Effort | Regression test |
|------|--------|--------|-----------------|
| 1. Durability + logging seam (F-08 atomic+timeout, F-03 atomic ledger, F-10 real sink) | F-03, F-08, F-10 | M | torn-write recovery; event emitted on failure |
| 2. State/status correctness (F-07 status checks, F-02 resume merge, F-01 blank modelling) | F-01, F-02, F-07 | L | resume assembles; blank page → parsed w/ DOM |
| 3. Fault containment (F-09 pool rebuild, F-11 config threading) | F-09, F-11 | M | worker death → page retry, not run abort; config flows |
| 4. Throughput (F-05 once-per-doc median, F-06 open-once, F-04 per-page state) | F-04, F-05, F-06 | M | native cost flat in N; ledger cost flat in N |

Wave 1 first: without durability + observability, the other fixes cannot be
verified in production ("no silent failures" is unprovable if you cannot see or
recover from them).

---

## What was *not* a problem (verified)

- `SourceScan` correctly raises `SourceScanError` on 0-page / unreadable PDFs
  (`source.py:84,90`) — the "0-page fake success" hole is closed at the scan layer.
- The `docling_guard`/`docling_guard_status` silent-loss detector is genuine and
  does reject `FAILURE`/`SKIPPED`/empty-stub pages (`heavy_docling.py:43-59`).
- Per-page exception containment in the worker (`scheduler.py:74-81, 98-105`) does
  correctly turn a crashing page into `FAILED` rather than aborting the run — the
  gaps above are about *what happens after* (ledger durability, pool breakage,
  status semantics), not the in-worker catch itself.
