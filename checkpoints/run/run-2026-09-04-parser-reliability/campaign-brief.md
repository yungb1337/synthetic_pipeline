# Parser Reliability Campaign — run-2026-09-04-parser-reliability

## Objective
Make the page-centric parser (Module #1, ADR-013) **reliable, accurate, stable** across a WIDE range of publicly-available, non-PHI documents (papers, hospital-record-LIKE reports, clinical guidelines, forms, scans). Proven-in: a 500–1000 file local corpus, LLM-judged accuracy, error/memory monitoring, per-batch benchmarks, production-level (non-hardcoded, non-generalized) fixes.

## Standing directives (locked from user)
- **No PHI.** Public, de-identified, re-downloadable open-access files only (PMC/NIH/CDC/CMS/.gov, WHO.int, arXiv.org). Real patient records are OFF the table.
- **No hardcoding.** Every parser fix must generalize across the wide range of documents; a fix that only passes one doc is REJECTED.
- **Document everything.** Errors, memory spikes, unseen situations, outages, architecture fail-downs. Reports per run, benchmarks per batch.
- **Tooling in `scripts/`** with concise docstrings, reusable.
- **LLM judge** (lightweight Gemini) returns a summary over MULTIPLE metrics derived from the DOM (not just a pass/fail text reply).
- **Fix loop bounded at 3 rounds** per `docs/org-gate-protocol.md`; escalate to user at round 3.

## Hard facts (environment)
- Run dir: `checkpoints/run/run-2026-09-04-parser-reliability/`
  - `sources/` raw PDFs · `parsed/` DOMs · `judgment/` verdicts · `reports/` benchmarks/errors · `logs/` parser+download logs · `knowledge/` per-step notes
- RAM 16.5 GB · disk 23 GB free → corpus cap ≈ 1000 PDFs + parsed DOMs. Monitor.
- Parser CLI seam: `scripts/parse_folder.py --in <dir> --out <dir>` (page-centric, resumable, per-page Docling).
- Single-file: `scripts/parse_one.py --file <pdf> --out <dir>`.
- Gemini SDK: `google.generativeai` 0.8.6 (NOT `google.genai`).
- Key: `key.py` or `GEMINI_API_KEY` env — **MISSING (2026-09-04); judge blocked until provided.**

## Step plan (each step = its own knowledge note in `knowledge/`)
1. **Step 1 — Corpus seed + downloader.** `scripts/seed_corpus.py` (sources from stable open-access APIs: PMC OA, arXiv) + `scripts/download_curated_corpus.py` (retries, SHA256, resumable, `--limit`). Seed 50 immediately valid + parseable; expand to 200 → 500 → 1000 in batches after harness validation.
2. **Step 2 — Band-1 reliability run.** Parse seed (50) with page-centric seam; capture per-doc status, wall-time, page counts, memory spikes, `std::bad_alloc`, engine/routing band. Write `reports/benchmark-b01.md` + `logs/`.
3. **Step 3 — Judge harness.** `scripts/judge/llm_judge.py` — source PDF ↔ parsed DOM; multi-metric JSON verdict (completeness, fidelity, structure/reading-order, tables, references, OCR/scans, hallucination). `judgment/<doc>.json` + aggregated `reports/judge-summary.md`.
4. **Step 4 — Triage + production fixes.** Classify Critical/Major/Minor; fix parser-layer only; bound 3 rounds; re-run bench+jury per round; escalate if still failing.
5. **Step 5 — Scale.** Expand manifest 200 → 500 → 1000; re-benchmark + re-judge each batch.
6. **Step 6 — Final report.** `reports/final-report.md`: objective, corpus, benchmarks, judge metrics, fixes, remaining risk, run history. User-verifiable.

## Success = Definition of Done
- 500–1000 files local in `sources/`, manifest tracks url + sha256 + stratum + retrieved_at.
- Batch benchmarks + judge summaries for every batch, preserved append-only.
- All Critical/Major issues from the judge triaged: fixed (production-level, no hardcoding) OR explicitly deferred with reason.
- Full pytest suite green; `scripts/verify_failure_points.py` clean.
- Final report that the user can independently verify.