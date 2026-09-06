# Step 1 Knowledge — Corpus seed + download + first parse

**Run:** run-2026-09-04-parser-reliability · Date: 2026-09-06

## What was done
1. Built `scripts/seed_corpus.py` (Europe PMC + arXiv + curated .gov/.int discoverer).
2. Built `scripts/download_curated_corpus.py` (SHA256-verified, resumable, retry-logged).
3. Seeded manifest: **184 entries** (S1=45, S2=46, S3=46, S4=2, S5=45), all `europepmc` + 4 curated .gov/.int.
4. Built `scripts/llm_judge.py` (light Gemini multi-metric judge; runnable once key resolves).

## Verified facts
- Europe PMC OA PDF endpoint `https://europepmc.org/articles/<PMCID>?pdf=render` works and returns real PDFs (12/12 validated magic bytes + PyMuPDF open, 130 pages, all BMJ Open 2026 academic papers).
- Parser output store layout (page-centric, ADR-013):
  - `parsed/dom/<doc_id>/dom-v0.1.0.docJSON` — canonical DOM (Document model, Pydantic).
  - `parsed/raw/<sha256>.pdf` — immutable source.
  - `parsed/manifest/<doc_id>/plan.json` — page ledger + assembly status.
  - `parsed/images/<doc_id>/` — recovered images.
- Doc id = `d-<sha256[:16]>`, derived from source hash.
- CLI seam: `scripts/parse_one.py --file <pdf> --out <dir>` and `parse_folder.py`.

## First parse observations (PMC13218181.pdf, BMJ Open review, 7 pp)
- Route: **docling**; assembly `ok`; 7/7 pages; **136 blocks, 7 images, 0 tables, 0 refs**.
- Timings: detect 0ms · route 1060ms · scan 7ms · **plan 41569ms** · run 21552ms · assemble 91ms · total 64279ms.
- `RapidOCR returned empty result!` x2 on docling text pages (likely benign — no scanned pages; track as error-class signal).
- Key perf: first-load Docling model init inside `plan_ms` dominance is a **benchmark target** (warm λ vs first-run).

## Decisions / next
- Judge must verify tables/references against actual source (potential `tables:0/refs:0` false-negative class).
- Expand manifest toward 500–1000 via repeated seeding with rotated queries (avoids stale-dedupe).
- Escalate: Gemini key still missing (key.py not on disk). Judge blocked until user provides key.