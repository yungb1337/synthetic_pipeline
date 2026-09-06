# HANDOFF — parser reliability campaign (for a fresh instance)

> **Read this first.** This file lets a new Claude instance resume the parser
> reliability campaign cold, without the prior conversation context. Written
> 2026-09-06 during `run-2026-09-06-parser-b1b2-fixes`.

---

## 1. Mission context (30-second brief)

Repo: `c:\Users\Asus\Downloads\projects\22_07` — **MedFactory AI** synthetic data
factory. Current goal (user's expanded prompt, binding): make the Parser
**reliable, accurate, stable** on a **wide corpus of 500–1000 REAL public
documents** (medical/academic PDFs). Quality verified by an **LLM judge**
(source PDF vs parsed DOM → multi-metric summary). Every run is benchmarked and
documented; fixes must be **production-level** (no per-doc hardcoding, must
cover the whole WIDE range). Full standing brief: `project_memory/active_objective.md`
(this file still names the old run; see §6 to update it).

User directives that always apply:
- Use real publicly available data (Europe PMC / PMC open-access, health admin PDFs).
- LLM judge = Gemini **`gemini-3.5-flash-lite`** (LIGHT model — never the expensive one).
- **Never echo or log the API key value.** Key lives in gitignored `key.py`
  (`key = "..."`, lowercase attribute). Judge resolver order: `--api-key` >
  `$GEMINI_API_KEY` > `key.py`.
- Keep ALL scripts in `scripts/` with concise docstrings; monitor logs; document
  every error/memory-spike/unseen situation; benchmarks + reports per run.
- User will verify everything on their end from the artifacts.

## 2. How to run things (Windows)

- Python: `.venv/Scripts/python.exe` (the venv, NOT system python).
- Tests: `.venv/Scripts/python.exe -m pytest tests/ -q`
- Parser CLI seam: `scripts/parse_folder.py <sources-dir> <parsed-store-dir>`
- Benchmark harness: `scripts/run_parser_benchmark.py --in ... --out ... --batch bNN --reports ... --heavy-concurrency 1`
- Judge per-doc: `scripts/run_judge_batch.py ...`
- Verify failure points: `.venv/Scripts/python.exe scripts/verify_failure_points.py`
- **Hardware constraint:** ~16.5 GB RAM, ~96% disk full. Paging-file exhaustion
  (WinError 1455) killed parses before. **Run parse + judge + download
  SEQUENTIALLY, never concurrently.** Use `--heavy-concurrency 1` (single Docling
  worker). Docling heavy worker RSS can reach 2.7–4.2 GB; a single engine build
  costs ~11 s (model load).

## 3. Corpus state (Fact)

| Item | Where | Count |
|------|-------|-------|
| Seed manifest (append-only, pinned URLs + SHA-256) | `checkpoints/run/run-2026-09-04-parser-reliability/sources/manifest.json` | 184 entries |
| Downloaded full corpus | `checkpoints/run/run-2026-09-04-parser-reliability/sources/pdf/` | 70 PDFs |
| Benchmark snapshot b01 | `checkpoints/run/run-2026-09-04-parser-reliability/sources/pdf_snapshot_b01/` | 36 PDFs |
| Snapshot purpose | subset over which `.gitignore`ed architectures, used for the clean re-run | — |

Corpus tools (own docstrings):
- `scripts/seed_corpus.py` — build the manifest.
- `scripts/download_curated_corpus.py` — resumable downloader, SHA check.
- Toward the 500–1000 file target: MORE downloading is needed (now at 70/184 of
  the curated manifest). **User must approve new URL sets before bulk download**
  (already-manifested entries are pre-authorized).

## 4. Where the parse results live

Store layout under a run dir's `parsed/`:
- `raw/<sha256>.pdf`, `dom/<doc_id>/dom-*.docJSON`, `pages/<doc_id>/pN/page-*.docJSON`,
  `images/`, `manifest/<doc_id>/plan.json` (the page ledger + assembly status).
- Clean-run store / reports:
  `checkpoints/run/run-2026-09-06-parser-b1b2-fixes/parsed/` + `reports/`.
- Poisoned evidence store (old cascade, keep as-is):
  `checkpoints/run/run-2026-09-04-parser-reliability/`.

## 5. Benchmarks (b01 pre-B4 → b02 post-B4)

`checkpoints/run/run-2026-09-06-parser-b1b2-fixes/reports/`:
- **b01** (pre-B4): `benchmark-b01.md` — 36 files, `ok=16 failed=0 dead=0 unparsed=0
  partial=20`, wall=504.8 s, peak=4239 MB, parser exit 0. 2603 blocks, 27 tables.
  ALL 20 partial docs are docling-route with scattered dead pages (e.g.
  d-5b1a5a60d972337e: 8/17 assembled, dead [2,3,4,6,7,9,12,14,15]) — **67 dead pages**
  blamed on `engine_unavailable`.
- **b02** (post-B4): `benchmark-b02.md` — SAME 36-file snapshot, `--heavy-concurrency 1`,
  fresh out dir `parsed-b02/`: **ok=36 failed=0 dead=0 unparsed=0 partial=0**, wall=599.5 s,
  peak=4504 MB, parser exit 0. **7287 blocks, 82 tables** (only 2603/27 before).
- A/B compare: `reports/benchmark-b01-vs-b02.md`.

**Why b02 wins:** both runs hit the SAME transient `std::bad_alloc` / `bad allocation`
class under paging-file pressure (b02 logged 153 error-signal lines; b01 logged 130+). But
B4 classified per-page convert failures as retryable `docling_convert` → assembler retried →
recovered. The error stream did NOT get quieter; the pipeline became **self-healing**. A
re-run in-process (RAM free, engine builds in ~11 s) proved those pages convert SUCCESS — the
old `engine_unavailable` label was a MISLABEL.

### Judge leg result (same run, accuracy)
On the same snapshot, the LLM judge (gemini-3.5-flash-lite) scored all 36 DOMs:
**0 FAIL, PASS=29 / PASS_WITH_ISSUES=7**, all 13 issues minor. Metric means (full 36-doc
re-judge with the fixed preview — see `judge-summary-b02.md`): completeness 0.972, fidelity
0.982, structure 0.945, references 0.954, scans_ocr 1.000. **Tables: all-doc mean 0.767; among
the 28 docs that genuinely have tables, mean 0.950 with zero docs below 0.90** — the residual
gap is only the 8 table-less docs scoring "not evaluable". **The one real finding was a judge-input artifact, not a parser bug:** `summarize_dom` sent the model only table dims →
`tables` scored 0.631 (see §6-addendum "Judge leg"). Fixed + re-judged: the 4 docs that had
scored 0.0 with real tables (2/1/3/7 tables) judge at 1.0 / 1.0 / 0.95 / 0.95, and the spread
of false "missing table" issues disappeared (issues 22→13, PASS 23→29).

## 6. B4 — root cause of the mislabels (DONE — fixed + verified 2026-09-06)

### Facts (verified by repro, not inference)
1. `app/parser/loaders/docling_loader.py::convert_path` (lines ~1167–1189) returns
   **`None` from TWO different causes**:
   - `get_engine() is None` → genuine engine outage (correct to call "unavailable").
   - `engine.convert()` raised `Exception` → any per-page transient failure,
     swallowed (`except Exception: return None`).
2. `app/parser/engines/heavy_docling.py::HeavyDoclingEngine.process` (line 36)
   maps ANY `None` → `PageResult(FAILED, errors=[{category:"engine_unavailable"}])`.
   → a single per-page convert() hiccup is labeled "the whole engine is down".
3. `app/parser/assembler.py::_retry_pages` (B2, lines ~255–265) SKIPS any page
   whose error category == `engine_unavailable`. So the B2 skip — which is meant
   to avoid futile retries during a real outage — now **skips recoverable pages**.
   That is why the clean run left 67 pages dead while the engine itself is fine.
4. `app/parser/scheduler.py::measure_footprint` (line 160) also calls
   `convert_path`; when it hits the convert() error it is inside a broad `try`
   returning `None` → `derive_heavy_concurrency` returns the safe floor (1).
   No behavior change needed there.

### Completed fix (production-level, no per-doc special-casing)
1. `convert_path` now only returns `None` for genuine engine-unavailable
   (`get_engine() is None`). A per-page `convert()` failure **raises a typed
   `DoclingConvertError(msg, page=n, caused=exc)`** carrying the real error text
   (`app/parser/loaders/docling_loader.py` — class defined + exported there).
2. `HeavyDoclingEngine.process` catches the typed error and emits category
   **`docling_convert`** (retryable) with the real message; `engine_unavailable`
   is reserved strictly for the `None` case (`app/parser/engines/heavy_docling.py`).
3. `assembler._retry_pages`: NO change needed — it already only skips
   `engine_unavailable`. With `docling_convert` distinct, those pages retry
   normally (in-process, second/third attempt) — exactly the recoverable behavior.
   Proven by b02: 36/36 ok.
4. Tests added: `test_heavy_docling_convert_error_is_retryable_category`,
   `test_retry_pages_retries_docling_convert_but_skips_engine_unavailable`
   (`tests/test_page_centric.py`). Full suite GREEN: `pytest tests/ -q` exit 0
   (only 2 expected skips). The previously-"hanging" pytest test is NOT the B4
   code — a stdlib-faulthandler probe of the exact body ran clean in 16.8 s
   (`scripts/_probe_hang.py`).
5. Exception owner: `app/parser/loaders/docling_loader.py` — single owner of the
   engine seam.

### Naming
Use the exact module path from the repo (read the file first if names drift):
`app/parser/loaders/docling_loader.py`. Do not confuse with the temp-dir
`dictlon_loader.convert_bytes` helper (lines ~1130–1145) — different function.

### Judge leg (accuracy) — tables metric artifact, fixed in tooling (NOT a parser bug)

**Finding:** dan judge summary showed `tables mean=0.631`. 12 docs at `tables=0.0`; of those,
**4 have DOM `tables_total>0`** (1,2,3,7 tables — verified against the b02 store) yet judge 0.
The parser had the tables; the judge input did not.

**Root cause:** `scripts/llm_judge.py::summarize_dom` passed the model only table dims
(`T1:6r x 3c`), no cell content → the model applied "tables: 0..1 (use 0 when not evaluable)"
→ 0. Latent bug: the dims lambda used per-page `enumerate`, so every page's first table
re-labeled T1.

**Fix:** `summarize_dom` now emits `tables_preview` — real header cells + first 2 data rows
per table (24-char truncation, 1200-char budget), a **global** sequential T-index, and
`tables_preview_note` telling the model truncation is a preview limit, never a defect.
Constants: `_CELL_CHARS=24 _TABLE_BUDGET=1200 _TABLE_PREVIEW_ROWS=2`. Tests:
`tests/test_llm_judge.py` (7 tests, PASS).

**Empirical proof:** re-judging the 4 artifact docs lifts tables 0.0 → **1.0 / 1.0 / 0.9 /
0.95**. The metric now isolates parser quality. Full 36-doc re-judge with the fixed preview →
`judge-summary-b02.md`: tables mean rises, verdicts stay 0 FAIL / minor-only. Residual
per-call variance of the light model (~0.05) is expected.

**Design note (metric lower bound):** `tables` (and the other metrics) measure what the
*bounded preview* lets the model see. A score of "not evaluable"-0 on a no-table doc is
correct; on a table-bearing doc it now means the model saw real cells. If a doc's real table
content exceeds the preview budget, the score is still a lower bound — re-check the
`tables_preview_note`/`tables_preview` fields in the stored judgment to distinguish that from
a parser defect.

## 7. Already-fixed production bugs in this run (KEEP — do not revert)

- **B1 (durability / "append, never destroy")** — FAILED/DEAD re-parse must never
  clobber a durable OK page. Two edits, unit-tested PASS:
  - `app/parser/storage_pages.py::PageStore.put_page` — refuse to write a
    FAILED/DEAD record over an existing OK page (returns the existing path).
  - `app/parser/scheduler.py::Scheduler._collect` — restore the prior OK result
    (artifact + ledger) over a transient FAILED/DEAD result before persisting.
- **B2 (futile retry)** — `assembler._retry_pages` skip pages whose failure
  category is `engine_unavailable` (genuine outage) → don't re-run a cached-False
  engine pointlessly. (Caveat from §6: this skip was also wrongly catching
  recoverable pages because of B4 — fixing B4 re-separates the two.)
- **B3 (misleading downgrade)** — covered by B1+B2: a poisoned re-parse no longer
  downgrades a good doc to dead.
- Root-cause context (writeup in `RUN_BRIEF.md` in this run dir): the run-2026-09-04
  mass outage was a resource-exhaustion cascade (two parses + two downloaders +
  an orphan 2.7 GB parse on a 15.4 GB box → paging-file exhaustion → docling
  model builds failing in workers → every page `engine_unavailable`).

## 8. Immediate next steps (in order)

**Status (2026-09-06, ~09:10Z):** B1/B2/B3/B4 DONE, full suite GREEN, benchmark b02
**36/36 ok** (vs b01 16/36). Judge leg DONE: 36/36 judged, 0 FAIL, all minor
issues. The tables-metric outlier was triaged to a **judge-input artifact** and
fixed in tooling (see §6 "Judge leg"). Remaining: final report.

1. ~~Implement B4~~ **DONE** (see §6 completed-fix).
2. ~~Verify B4~~ **DONE** — full `pytest tests/ -q` exits 0 (only 2 expected skips).
   In-process reconvert probe clean in 16.8 s (`scripts/_probe_hang.py`).
3. ~~Re-run benchmark~~ **DONE** — `scripts/run_parser_benchmark.py --batch b02
   --heavy-concurrency 1` on the clean 36-PDF snapshot → `reports/benchmark-b02.md`:
   ok=36/36, partial=0 (was 20), blocks 2603→7287, tables 27→82. A/B in
   `reports/benchmark-b01-vs-b02.md`.
4. **Judge leg (accuracy) — IN FLIGHT**: `scripts/run_judge_batch.py` over the b02
   store → `judgment/<doc_id>.json` + `reports/judge-summary-b02.md`. Model
   `gemini-3.5-flash-lite`, key from gitignored key.py. **DO NOT ECHO/LOG THE KEY.**
   - Watch the **free-tier quota**: limit 15 req/min on this model → the 429 fix
     (retry w/ reported delay, exit 4 skip-and-continue, --pacing 4s default) is
     already in `scripts/llm_judge.py` + `scripts/run_judge_batch.py`. 16/36 judged
     PASS/PASS_WITH_ISSUES, fidelity 0.95–0.99 before the quota hit.
   - Known false-negative: references present as text blocks but absent from the
     structured `references` field → judge scores references low — a *metric*
     artifact, not necessarily a parser bug.
5. **Triage critical/major judge findings** → fix loop (bounded ≤3) → re-verify.
6. **Final report** + benchmark history + errors log + knowledge notes per step.

## 9. Where to write artifacts (run conventions)

- This run: `checkpoints/run/run-2026-09-06-parser-b1b2-fixes/`
  - `reports/benchmark-bNN.md` (per batch), `reports/benchmark.md` (append-only one-liners),
    `reports/errors.md` (error-signal stream).
  - `judgment/` per-doc verdicts + summary.
  - `parsed/` store (already exists from b01).
- Update `project_memory/active_objective.md` run id + notes to reflect THIS run; the
  orchestrator (`/dev-team`) reads it next.
- Append a memory entry to `project_memory/MEMORY.md` after the campaign (per the memory instructions at the top of the system prompt: `C:\Users\Asus\.claude\projects\c--Users-Asus-Downloads-projects-22-07\memory\`).

## 10. Guardrails to re-verify before ANY commit

- No per-document / page-number / title special cases in the fix.
- Modular monolith, page-centric model (ADR-013), `DocumentValidator` hard gate,
  dead-lettering, idempotent resume — all preserved.
- `.gitignore` covers `key.py` (confirmed present but never committed).
- Append, never destroy. Every artifact versioned or append-only.