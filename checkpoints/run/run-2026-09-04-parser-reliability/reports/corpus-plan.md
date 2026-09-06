# Corpus Plan — run-2026-09-04-parser-reliability

## Goal
500–1000 locally-stored PDFs, diverse enough to stress the parser's real failure modes — not just "many files". Every file must be public, non-PHI, and re-downloadable from its manifest.

## Selection rationale (why these, not random PDFs)
The parser's known risk surfaces (ADR-013, F-01..F-11) are:
- **Reading order** on multi-column pages (native heuristic is top→bottom only)
- **Tables** (dense/borderless, multi-page, header rows — TableFormer FAST vs ACCURATE tradeoff)
- **Scans / OCR** (RapidOCR v6, `OCR_MAX_EDGE=2000`, Docling `scale=1.5`)
- **Page-count stress** (page-centric ledger, `assembled_set==expected_set` gate)
- **Mixed image+text** and embedded figures

Corpus is stratified to cover each surface. Diversity is the anti-hardcoding guarantee: a fix that passes only one document is rejected.

## Strata (target mix for 500–1000)

| Stratum | Share | Examples | Stress |
|---------|-------|----------|--------|
| S1 Multi-column academic reviews | ~25% | PMC open-access systematic reviews, NEJM open articles | Columns, refs `[n]`, citations |
| S2 Table-dense reports | ~20% | CDC MMWR / surveillance tables, Census tables | `cell_bboxes`, header rows |
| S3 Clinical-guideline PDFs | ~20% | NIH / NICE / WHO guideline PDFs | Headings, lists, footnotes, mixed layout |
| S4 Scanned / image-heavy | ~15% | CMS synthetic forms (CMS-1500), Synthea-generated discharge PDFs | OCR path |
| S5 Mixed / edge cases | ~20% | arXiv medical imaging papers, long 50–200 page reviews | Page count, figures |

## Curation method (no PHI)
- **Allowed sources only**: PMC/NIH/CDC/CMS (.gov), WHO.int, arXiv.org open-access.
- **Blocked**: any hospital EHR export, MIMIC/PHI-derived PDFs, paywalled publisher PDFs, user-uploaded PHI.
- Every file is recorded in `sources/manifest.json` with `url`, `sha256` (filled on download), `stratum`, `expected_pages` (optional), `retrieved_at`.

## Manifest + script
- `sources/manifest.json` — single source of truth, append-only. Seed with ~50 entries first, expand to 500–1000 incrementally so the harness can be validated continuously.
- `scripts/download_curated_corpus.py` — deterministic downloader: retries, SHA check, progress log, `--limit`, `--resume`, `--manifest`, `--out`.

## Verification
- `scripts/monitor_parser_logs.py` captures per-doc exit status + `std::bad_alloc` / ONNX / traceback lines into `logs/run-*.log` and `reports/errors.md` (nothing dropped).
- Corpus is re-parsed by `scripts/run_parser_benchmark.py` (`--in sources --out parsed`) using the page-centric CLI seam; output lives under `parsed/` (store layout `raw/dom/images/pages/manifest`).

## Growth plan
1. Seed 50 (this report; immediately downloadable + parseable).
2. Validate harness (reliability/perf/judge) on 50.
3. Expand manifest to 200 → 500 → 1000 in batches; re-benchmark each batch.
4. Every expansion appends to `reports/benchmark.md` (so history is preserved).

## Risks
- External URLs drift/dead links → manifest retains `retrieved_at` + SHA; dead links are replaced, not silently skipped.
- Very large PDFs may still OOM even with per-page Docling + OCR downscale (`OCR_MAX_EDGE=2000`) → expected to be captured in `reports/errors.md` and triaged, not hidden.
