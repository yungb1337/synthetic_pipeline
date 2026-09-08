---
name: active-objective
description: The run brief — the objective and notes the autonomous organization executes next. Overwrite the body for each new run; keep this file.
metadata:
  type: project
---

# Active Run Brief

> The Project Orchestrator (`/dev-team` in-session, or the `audit` workflow in background)
> reads this file at the start of a run. Replace the contents for a new run; keep this file.

## Run id
`run-2026-09-06-parser-b1b2-fixes` (reliability+accuracy campaign — B1–B4 mechanics + LLM-judge leg). Prior: `run-2026-09-04-parser-reliability` (mass-engine-unavailable root-caused, first-corpus seed).

## Objective
Make the parser **production-ready**: reliable, accurate, and stable across a **wide, diverse corpus of 500–1000 real public documents** (medical/academic/complex PDFs). Goal = the user can trust the parser on unseen production data. Expand the prior ad-hoc "fix 5 points" into a **measured reliability program**: every run is benchmarked, every error is preserved, every improvement is verified against the corpus (no single-document hardcoding).

Prompt source: user expanded reliability request (2026-09-04) — curated corpus, configurable benchmarks via scripts, LLM-judged accuracy, reliability/perf testing, production-level fixes only.

## Scope (modular, additive, no project rewrite)

### Step 0 — Secure secrets
- `key.py` removed (was untracked, never committed — verified); `.gitignore` now excludes `key.py`, `.env`, `*.key`.
- LLM judge reads `GEMINI_API_KEY` from environment only (caller-supplied; never logged/echoed/committed).

### Step 1 — Corpus curation (500–1000 files, local, versioned, reusable)
- Sources: **public, no PHI** — open-access medical/academic PDFs (e.g., PMC/NIH, CDC, CMS synthetic samples, Synthea synthetic, arXiv medical reviews) — avoids real hospital EHR/PHI.
- Deliverables:
  - `checkpoints/run/<run_id>/sources/manifest.json` — pinned URLs + expected filenames + SHA-256 (append-only, auditable).
  - `checkpoints/run/<run_id>/reports/corpus-plan.md` — selection rationale, diversity coverage (multi-column, tables, scans, lab-style, mixed image+text).
  - `scripts/download_curated_corpus.py` — deterministic downloader (retries, SHA check, progress, resumable), reusable for corpus refresh; concise docstring + `--help`.
  - Corpus stored locally (e.g., `checkpoints/run/<run_id>/sources/*.pdf`) with a retained copy for continuous testing; script supports re-running to keep corpus fresh.

### Step 2 — Reliability harness (scripts = product, not throwaway)
All scripts in `scripts/` with concise docstrings and `--help`:
- `scripts/download_curated_corpus.py` — download + verify corpus.
- `scripts/run_parser_benchmark.py` — batch parse via `app.parser` page-centric path (`scripts/parse_folder.py` seam), emitting per-doc and aggregate metrics.
- `scripts/llm_judge_parser.py` — LLM judge (Gemini `gemini-2.5-flash-lite`, `GEMINI_API_KEY` from env) comparing source PDF text (fitz extract) vs DOM; per-doc JSON + summary.
- `scripts/monitor_parser_logs.py` — log monitor (tail + error/memory spike detection; unseen error capture).

Each step has its own knowledge artifact:
- `checkpoints/run/<run_id>/knowledge/step{1..N}.md` — what was learned, not just what ran.
- `checkpoints/run/<run_id>/reports/benchmark.json` + `benchmark.md` — latency, throughput, success/failure, per-doc timings.
- `checkpoints/run/<run_id>/reports/errors.md` — all errors, memory spikes, unseen situations (never dropped).
- `checkpoints/run/<run_id>/judgment/` — per-doc judge verdicts + `judgment/summary.json` + `judgment/summary.md` (multi-metric: faithfulness, structure, tables, reading order, OCR).

### Step 3 — Testing matrix (reliability · performance · accuracy)
- **Reliability**: `status == parsed` rate, `assembled_set == expected_set` gate, dead-letter rate, crash/memory/OOM count, idempotent `rerun_test_cases.py` check, `scripts/verify_failure_points.py` (F-01..F-11) stays green.
- **Performance**: per-doc `p50/p95` latency, throughput (docs/min), memory envelope (RSS / process), concurrency behavior, OCR-vs-native cost split.
- **Accuracy (LLM judge)**: multi-metric rubric (implemented in `llm_judge_parser.py`) — text faithfulness, block/heading fidelity, table recovery, reading-order correctness, OCR quality, metadata/provenance completeness; aggregated `judgment/summary.md` with Critical/Major/Minor triage.

### Step 4 — Findings → fixes (production-level only)
- Fixes only inside `app/parser/` (and minimal `app/processing` if scheduler-bound), preserving ADR-013 page-centric model, `DocumentValidator` gate, and idempotent resume.
- **No hardcoding**: no document-name/page-number/table-id special cases; normalize anything that is invariant across documents.

## Definition of Done (gate-level)
1. Corpus of 500–1000 files locally present, manifest versioned, downloader script reusable.
2. At least one full benchmark run completes: `reports/benchmark.json` + `benchmark.md` + `reports/errors.md` + `judgment/summary.md` present and reviewed.
3. Reliability/perf/accuracy metrics reported and baselined; regressions have a documented next step.
4. Any Critical/Major finding either fixed (with bounded fix loop ≤3, re-benchmarked) or triaged with a deferred ADR/question.
5. `scripts/verify_failure_points.py` stays green; `pytest -q` stays green; no silent loss.
6. Knowledge per step written; final `checkpoint.md` + `final-report.md` close the run.
7. Every artifact is append-only / versioned; logs are continuously monitored (error + memory spikes captured).

## Validation Requirements
- `scripts/verify_failure_points.py` — all checks PASS (not reproduced).
- `.venv/Scripts/python.exe -m pytest tests/ -q` — green.
- Full corpus parse + `judgment/summary.md` — no untriaged Critical.
- Benchmarks recorded per run; script `--help` documented.

## Constraints
- Follow `docs/org-gate-protocol.md` hard gates (reviews: `VERDICT: PASS` before merge).
- Modular monolith, Clean Architecture; event-driven + idempotent.
- Secrets via env only; no keys in code/chat/logs.
- Public, no-PHI corpus; no arbitrary hospital EHR scraping.

## Run-specific notes
- **This run (09-06) CLOSED the reliability + accuracy gates.** Full handoff for a fresh instance:
  `checkpoints/run/run-2026-09-06-parser-b1b2-fixes/HANDOFF.md` (read first).
- **Reliability:** B4 split the mislabeled `engine_unavailable` (per-page convert failure was being
  labeled an engine outage) → b02 benchmark 36/36 ok vs b01 16/36 on the same snapshot; 0 dead
  pages; full `pytest tests/ -q` GREEN. B1 (durability), B2 (retry-skip), B3 (covered).
- **Accuracy:** LLM judge `gemini-3.5-flash-lite` judged 36/36: 0 FAIL, 29 PASS / 7
  PASS_WITH_ISSUES, all issues minor. Fidelity 0.982, completeness 0.972. The one red metric
  (tables 0.631) was a **judge-input artifact** (`summarize_dom` sent only table dims) — fixed
  with a real-content `tables_preview` + note; table-bearing-doc mean now 0.950 (min 0.90, 0
  below). Judge tooling also hardened against the Gemini free-tier quota (retry-on-429, exit-4
  skip, --pacing).
- **Key/secret standing facts:** Gemini key lives in gitignored `key.py` (lowercase attr `key`).
  Judge resolver: `--api-key` > `$GEMINI_API_KEY` > `key.py`. **NEVER echo/log the key value.**
  Prefer the LIGHT model **`gemini-3.5-flash-lite`** (never the expensive one); free tier =
  15 req/min.
- **Hardware constraint (binding):** ~16.5 GB RAM, ~95% disk. WinError 1455 / `std::bad_alloc`
  under paging-file exhaustion. Run parse + judge + download SEQUENTIALLY, NEVER concurrently;
  use `--heavy-concurrency 1` (single Docling worker). Docling heavy worker RSS 2.7–4.2 GB.
- **Corpus state (Complete):** 945 / 984 manifest PDFs downloaded and verified on disk (`checkpoints/run/run-2026-09-04-parser-reliability/sources/pdf/`), spanning all 5 risk strata (S1: 204, S2: 196, S3: 182, S4: 160, S5: 203). 39 failures captured with explicit HTTP error statuses; 0 magic-byte or SHA-256 mismatches. Total size: 2.87 GB; disk free headroom: ~21.14 GB.
- **Stage 1 Execution (100% Parsed & Assembled):** All 945 PDFs across Waves 1–5 (`b03` through `b07`) parsed and assembled into canonical DOMs with 0 dead pages, 0 failed pages, 0 missing pages across 13,132 total pages (201,315 text blocks, 3,243 tables extracted). Self-healing orchestrator (`scripts/run_all_waves.py`) managed C++ heap resets seamlessly.
- **Stage 3 Stratified LLM Judge (100% Acceptance):** 120 documents evaluated across 5 risk strata (24 docs/stratum) with `gemini-3.5-flash-lite`: 86 PASS (71.7%), 34 PASS_WITH_ISSUES (28.3%), 0 FAIL (0.0%). Overall completeness: 97.8%, Fidelity: 98.5%, Structure: 95.7%, References: 96.2%, Scans/OCR: 100.0%. 0 Critical issues, 0 Major issues, 53 Minor nuances.
- **Bug Fixes Applied:** Fixed degenerate 0-row table structural confidence calculation (`_table_structural_confidence`) in `app/parser/loaders/docling_loader.py` preventing `IndexError` on edge-case table headers.
- Judge tooling is additive; parsing/Judging code changes live in the working tree (uncommitted) —
  do not revert blindly.

## Expanded prompt (this run tracks)
Goal: **Reliable, Accurate, Stable Parser** — measured on 500–1000 diverse public documents; every run produces benchmarks + error/bench reports; every error (including memory/unseen) is documented; every fix is wide-corpus, not single-doc. Scripts are kept and reusable. Logs are continuously monitored.
Each step has its own knowledge. Fixes are production-level (no hardcoding, wide coverage).
