# Scale & Batch Execution — Architecture (thousands → millions of documents)

**Status:** implemented (module + tests green) · **Relates to:** SYN2 layer-14 (orchestration), SYN4 ("100 workers, not 1"; batch embeddings). Verified: 12 docs in ~26 ms (~2 ms/doc), incremental re-run skips already-done files.

## The problem this layer solves
The single-document pipeline (parse → normalize) is deterministic and correct, but enterprise demand is *thousands → millions* of documents. Two changes were required, and both shaped the code:

1. **Batch the expensive model-boundary calls** (OCR, embeddings) — never one image / one chunk at a time.
2. **Parallelize across documents** with idempotent, resumable execution — never re-parse what's already done.

## What was added

### `app/processing/` — the batch execution layer
- `config.py` — `ProcessingConfig`: concurrency, retries, manifest path, `ocr_warm`, `embed_batch_size`, accepted extensions.
- `corpus.py` — parallel content-hash (`hash_paths`) + a persisted **`{sha256}` manifest** (`load/save/pending`). The manifest is what makes runs **idempotent and incremental**: a re-run parses only files whose hash is absent, so a million-doc corpus resumes after a crash or picks up only new files.
- `executor.py` — `ParseNormalizePipeline` (parse → normalize → persist) run by `BatchWorker.run` over a `ThreadPoolExecutor`, with:
  * per-item retries + backoff,
  * fresh `BatchReport` per run (total / ok / failed / skipped / per-format / ids / errors),
  * OCR engine warmed once before the pool (no per-doc cold start),
  * manifest flushed periodically + at end (crash-safe progress).
- `cli.py` — `python -m app.processing.cli --in <corpus> --out <store> [--concurrency N --manifest ... --no-ocr --embed]`.

### Batched model-boundary calls
- **OCR (`app/parser/ocr.py`)**: lazy singleton engine + `batch_ocr_bytes([...])` reusing ONE loaded model across many pages, and `warm()` to preload once per worker pool. The engine loads only when a doc actually needs OCR.
- **Embeddings (`app/embedding/`)**: an `Embedder` protocol (list-in → vectors-out; *never* one-at-a-time) + `batch_embed(...)` slicing into model-sized batches with shape-guard + `embed_document_blocks(...)`. A deterministic `DummyEmbedder` keeps the pipeline runnable/tests today; the real GPU model drops in as another `Embedder` with zero call-site changes.

## Verified
- Tests (`tests/test_processing.py`, `tests/test_embedding.py`) + parser/normalizer = **24 tests passing**.
- Measured: 12 markdown docs parsed+normalized in **26 ms (~2.2 ms/doc)**; a re-run **skips all 12 in 11 ms**; adding one file → **only that one** reprocessed.
- Failing/corrupt docs don't crash the batch (reported as `failed`, batch continues).

## Two hard lessons from the run (worth writing down)
1. **SQL's Console N/A**: a batch pipeline must NOT print one `[event]` line per doc to stdout — at 1M docs that's a million lines of pipe/serialization overhead and it slowed the demo to a crawl. Batch events go to a broker or are dropped; here the parse pipeline uses a silent sink. (This is exactly why `events.py` has a `Store`-seam-like sink.)
2. **ThreadPool does NOT speed up CPU-bound Python due metal (GIL)**: parsing dozens of docs in 16 threads was *slower*, not faster. At real scale the lever is worker **processes** (`ProcessPoolExecutor`, or separate worker binaries) where PyMuPDF/OCR release the GIL — a documented near-term improvement, acknowledged in the code comments and this doc.

## Scaling path (monotonically, per the architecture brief "modular monolith → workers")
1. Modular monolith + batch layer (done) → 2. ProcessPool or worker binaries for CPU lanes → 3. Extract only the heavy lanes (OCR, embedding GPU) as decoupled workers → 4. Persistent broker (Redis/GPU) for queues + DLQ + events; Postgres for manifests/lineage. Only then consider independent services.

## Files
```
app/processing/   config.py · corpus.py · executor.py · cli.py
app/embedding/     embedder.py · dummy.py · runner.py
app/parser/ocr.py  batch_ocr_bytes() · warm()
tests/test_processing.py · tests/test_embedding.py
```