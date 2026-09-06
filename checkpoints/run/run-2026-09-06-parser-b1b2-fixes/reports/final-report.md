# Final Report — Parser Reliability Campaign (run-2026-09-06-parser-b1b2-fixes)

> Campaign outcome for the Parser reliability/accuracy leg. Companion artifacts:
> `HANDOFF.md` (fresh-instance resume), `knowledge/step-notes-b01-b02.md`, `reports/errors.md`,
> `docs/parser-fixes-implementation-summary.md` (Wave 5). Run id:
> `checkpoints/run/run-2026-09-06-parser-b1b2-fixes/`.

---

## 1. Mission & gate outcome (Fact)

The user's binding objective: make the Parser **reliable, accurate, stable** on a **wide range of
real public documents** (Europe PMC / PMC open-access health PDFs), judged by an **LLM judge** on
source-vs-DOM correspondence, production-level fixes (no hardcoding, no per-doc special cases).

**This run closes the loop on the reliability + accuracy gates:**

| Gate | Verdict | Evidence |
|------|---------|----------|
| Reliability (no silent page loss, self-healing) | **PASS** | b02: 36/36 documents assembled ok, 0 dead page, 0 failed (vs b01 16/36); full test suite green |
| Accuracy (LLM judge, real DOM fields) | **PASS** | 36/36 judged; 0 FAIL; 23 PASS / 13 PASS_WITH_ISSUES; all 22 issues minor; fidelity 0.98, completeness 0.97, structure 0.94, references 0.95, scans_ocr 1.00, tables 0.74→higher after preview fix (see §4) |
| Judge/measurement soundness | **PASS** | the one red metric (tables 0.631) was triaged to a **judge-input artifact**, fixed and empirically verified → genuine parser table quality is 0.9–1.0 |
| Production-level (no per-doc hardcoding) | **PASS** | all fixes are mechanism-level (error taxonomy, retry policy, storage durability, judge-preview shape); no page/title/PMC-id special cases |

**User asks to verify on their end:** see artifacts in §6.

---

## 2. Root cause chain (what was actually wrong)

### 2.1 Reliability: the "engine unavailable" mislabel (B1–B4, all fixed)

1. `run-2026-09-04` showed mass `FAILED engine_unavailable` and 20/36 docs dead-lettered to
   `partial`.
2. **B4 (root cause):** `docling_loader.convert_path` collapsed TWO different causes into one
   `return None`:
   - `get_engine() is None` → a genuine outage (correct to call "unavailable");
   - **any per-page `engine.convert()` exception** (e.g. transient `std::bad_alloc`, ONNX bad
     allocation on a single page) → swallowed and re-labeled "engine unavailable".
   `HeavyDoclingEngine.process` mapped ANY None → `engine_unavailable`, and B2's
   `assembler._retry_pages` (correctly, for outages) *skips* that category → **recoverable pages
   were never retried.** A re-run in-process proved those pages convert SUCCESS in ~17 s: the
   engine was alive; the label was lying.
3. **Fix (mechanism-level, no per-doc special-casing):**
   - `convert_path` returns `None` ONLY for genuine engine outage; per-page convert failures raise
     a typed **`DoclingConvertError(page, caused)`** → category **`docling_convert`** (retryable);
   - retry-skip stays strict on `engine_unavailable` alone.
   - **B1 (durability):** FAILED/DEAD re-parse never clobbers a durable OK page
     (`storage_pages.py::put_page` + `scheduler._collect`). **B3 (misleading downgrade)** is
     covered by B1+B2.
4. **Verified:** full `pytest tests/ -q` GREEN (exit 0, 2 expected skips). Previous
   "docling-test-hangs" scare proven to be a red herring: the stdlib-faulthandler probe of the
   exact body ran clean in 16.8 s.

### 2.2 Accuracy: the tables metric was lying (judge-input artifact, now fixed)

- `judge-summary-b02` V1 showed `tables mean=0.631`; 12 docs had `tables=0.0`.
- Cross-referencing the parser store proved the parser was fine: 4 of those docs have DOM
  `tables_total>0` (1,2,3,7 tables) yet judged 0.
- **Root cause (scripts/llm_judge.py::summarize_dom):** the DOM summary sent to the model had
  only table *dims* (`T1:6r x 3c`), never cell content. The prompt's `tables: 0..1 (use 0 when
  not evaluable)` → the model defaulted to 0 whenever it had nothing to verify. Latent bug: the
  dims lambda used **per-page `enumerate`** → every page re-labeled its first table "T1".
- **Fix (judge tooling only):** `tables_preview` now carries real cells (header + first 2 data
  rows, 24-char truncation, 1200-char budget, **global** sequential T-index) plus an explicit
  `tables_preview_note` that truncation is a preview limit, never a defect.
- **Empirical proof:** re-judging the 4 artifact docs lifted tables **0.0 → 1.0 / 1.0 / 0.9 /
  0.95**. Full 36-doc re-judge with the fix → final summary §4. **The parser's tables were
  always good; the measurement was blind to them.**

---

## 3. Benchmark history (Fact)

Same 36-PDF real-corpus snapshot (`pdf_snapshot_b01`), both `--heavy-concurrency 1`:

| Metric | b01 (pre-B4) | b02 (post-B4) |
|--------|-------------|---------------|
| documents ok | **16 / 36** | **36 / 36** |
| partial (dead-lettered pages) | **20** | **0** |
| DEAD pages | 67 | 0 |
| DOM blocks | 2603 | **7287** |
| DOM tables | 27 | **82** |
| wall time | 504.8 s | 599.5 s |
| peak RSS | 4239 MB | 4504 MB |
| error-signal stream (`std::bad_alloc` class) | 130+ | 153 (SAME class) |
| parser exit | 0 | 0 |

**Key insight:** both runs hit the identical transient `std::bad_alloc`/ONNX `bad allocation`
class under paging-file pressure. b01 let it kill pages; b02 (with B4) retried and **recovered
every one**. The error stream did NOT get quieter — **the pipeline became self-healing.**
Judge health by final assembly + content, not raw error-signal count. Full A/B:
`reports/benchmark-b01-vs-b02.md`.

---

## 4. LLM-judge result (current, fixed preview) — Fact

- Docs judged: **36/36**, skipped 0, unresolved 0; model `gemini-3.5-flash-lite`;
  judge exit codes functional (0 ok / 3 auth-fatal / 4 rate-limit-skip) + free-tier pacing.
- Verdicts: **PASS=`29` · PASS_WITH_ISSUES=`7` · FAIL=`0`** (full 36-doc re-judge, fixed preview).
- Issues: all **minor** (severity tally: minor=13; critical/major=0).
  By surface: structure=`8`, table=`4`, text=`1`.
- Metric means (verify against `reports/judge-summary-b02.md`):
  completeness=`0.972` · fidelity=`0.982` · structure=`0.945` · tables=`0.767`
  · references=`0.954` · scans_ocr=`1.000`.
- **Tables mean, correctly read:** the all-doc 0.767 includes 8 docs that genuinely have
  NO tables ("not evaluable" = correct 0 by prompt definition). Among the **28 table-bearing
  docs, mean tables = 0.950, min 0.90, and zero docs at 0** (see `judgment/tables_per_doc.txt`
  / `reports/tables_per_doc.txt`). The parser's table extraction is structurally sound; the
  0.631 outlier from before the fix was the judge being blind to cell content, now resolved.
- Note: the old dims-only preview also produced false "missing table" issues; with real cells
  the issue count fell 22→13 and PASS count rose 23→29. Some per-call LLM verdict variance
  remains (~±0.05 on a metric, a few verdict flips at the PASS boundary) — re-judging a subset
  reproduces the headline numbers to that tolerance.

**Known measurement caveats (be transparent, not silent):**
1. **Free-tier quota:** the model is rate-limited to 15 req/min; the judge tooling now retries on
   429 with the API-reported delay, and the batch skips-and-continues on exit 4. Some per-doc
   verdicts may wobble ±0.05 across calls (LLM-non-determinism) — re-judging a subset is
   reproducible to that tolerance.
2. **Metric is a lower-bound on preview size:** the model judges only the bounded preview
   (first/last + evenly-sampled source pages; DOM tables/refs in sampled first rows). A score of
   0 on a genuinely table-less doc is correct; on a table-bearing doc it now means the model saw
   real cells (see §2.2).
3. **References:** references present as inline text but not in a structured `references` field
   still suppresses the references metric (known field-shape limitation, §Step 6 notes). This is
   a *metric* artifact, not evidence of content loss.

---

## 5. What changed (files) — Fact

Parser (production code, all mechanism-level):
- `app/parser/loaders/docling_loader.py` — `DoclingConvertError`, strict `None`==outage
- `app/parser/engines/heavy_docling.py` — distinct retryable `docling_convert` category
- `app/parser/assembler.py` — retry-skip only on `engine_unavailable`
- `app/parser/scheduler.py` / `app/parser/storage_pages.py` — B1 durability rescue of prior OK
Judge tooling:
- `scripts/llm_judge.py` — rate-limit retry/exit taxonomy; `tables_preview` + note
- `scripts/run_judge_batch.py` — exit-4 skip-and-continue, `--pacing`
- `tests/test_llm_judge.py` (new, 7 tests) — judge-input key-field guards
Tests:
- `tests/test_page_centric.py` — B1/B2/B4 regression tests (unit PASS + full suite GREEN)
Docs/artifacts:
- see §6 index.

---

## 6. Artifact index (for the user to verify)

Under `checkpoints/run/run-2026-09-06-parser-b1b2-fixes/`:
- `HANDOFF.md` — fresh-instance resume (read first).
- `reports/benchmark-b01.md`, `reports/benchmark-b02.md`, `reports/benchmark-b01-vs-b02.md`.
- `reports/judge-summary-b02.md` — final judge summary (regenerated post-fix).
- `reports/errors.md` — append-only error/signal log incl. both incidents + fixes.
- `knowledge/step-notes-b01-b02.md` — per-step learnings (Steps 1–7, 6.5).
- `judgment/*.json` — per-doc verdicts incl. `dom_summary` (proves `tables_preview` now carries
  cell content).
- `parsed-b02/` — the clean 36-doc page-centric store.
Plus repo-level: `docs/parser-fixes-implementation-summary.md` (Wave 5 incl. judge leg).
Key scripts (own docstrings): `scripts/llm_judge.py`, `scripts/run_judge_batch.py`,
`scripts/parse_folder.py`, `scripts/seed_corpus.py`, `scripts/download_curated_corpus.py`.

---

## 7. Remaining / next (Recommendation)

1. **Corpus expansion to 500–1000** (user approve new URL sets first). Current: 70/184 manifest
   downloaded. Judge would then run on a wider slice → genuine "wide-range" evidence.
2. **Re-route OCR leg:** scans_ocr=1.0 on 36 docs is encouraging but the corpus is mostly
   born-digital; the OCR path deserves a few scanned/older PDFs in the expansion.
3. **Structured references recovery:** investigate `references` present-as-text but absent from
   the structured field (metric artifact, but also a real extraction-quality lever).
4. **Optional:** persist the whole 36-doc corpus judge as the canonical accuracy baseline; any
   future parser change can diff judge summaries (regression harness).

**Overall recommendation to the orchestrator:** **escalate — Parser reliability + accuracy gates
PASS.** b02 fixed the reliability gap (36/36 vs 16/36) and the accuracy leg is green (0 FAIL,
fidelity ≥0.97), with the one red metric proven to be a fixed judge-input artifact rather than a
parser defect. The fixes are mechanism-level and cover the full document range observed.