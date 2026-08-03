---
name: run-synthetic-data-factory
description: Build, run, and drive the Synthetic Data Factory — parse documents to a canonical DOM, normalize text, batch-process a corpus, and check the GPU embedder. Use when asked to run/start/test the parser, normalize a document, batch-process documents, verify embeddings, or check the pipeline end-to-end.
---

The Synthetic Data Factory is a Python modular monolith: a hospital-document parser
(`app.parser` → canonical DOM), a text normalizer (`app.normalizer`), a parallel batch
layer (`app.processing`), and a GPU embedding seam (`app.embedding`, BGE-M3 / 1024-dim).
It's a CLI/library — there is no server or GUI. The agent-facing way to drive it is the
**smoke driver** [driver.py](.claude/skills/run-synthetic-data-factory/driver.py), which
runs every stage of the real pipeline (parse → normalize → batch → incremental re-run)
on freshly written sample docs and asserts on the output.

All paths below are relative to the repo root. Use the venv python — on Windows:
`.venv/Scripts/python.exe`; on POSIX: `.venv/bin/python`.

## Prerequisites

- Python ≥ 3.11 (3.14.6 verified), `pip`, `git`.
- GPU + CUDA 12.x only for the *embedding* path (RTX 3050 4GB verified). Everything
  else runs CPU-only.

## Setup

Provisioned already on this machine. For a fresh clone:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate ; POSIX: source .venv/bin/activate
pip install -r requirements.txt
```

GPU embeddings (optional — skip on machines without a GPU; the pipeline falls back to a
deterministic `DummyEmbedder`):

```bash
pip install --index-url https://download.pytorch.org/whl/cu126 torch
pip install -r requirements-gpu.txt
PYTHONPATH=. python scripts/download_models.py      # -> models/bge-m3 (git-ignored)
```

## Run (agent path)

The smoke driver is the primary interface. Run it from the repo root with the venv python:

```bash
.venv/Scripts/python.exe .claude/skills/run-synthetic-data-factory/driver.py
```

Expected: `== smoke driver OK — all pipeline stages green ==`. It:

1. writes sample docs to a scratch dir (markdown note, CSV labs, JSON discharge),
2. parses them → asserts `parsed 3/3 documents` and 3 DOM files in the store,
3. normalizes one DOM → asserts a `*.norm.json` is written,
4. batch-processes a 5-doc corpus → asserts `parsed+norm : 5`,
5. re-runs the batch with the same `--manifest` → asserts `skipped: 5` (incremental),
6. imports the library directly (`detect()` on a PDF magic byte → `pdf`).

| flag | what it adds | cost |
|---|---|---|
| `--embed` | loads BGE-M3 on the GPU and embeds sample text (prints `device` / `dim` / `vec dims`) | slow first load (~1 s + model load) |
| `--test` | runs the full pytest suite afterwards | ~30–60 s |
| `--keep` | keeps the scratch workspace (default: deletes on success) | — |

```bash
.venv/Scripts/python.exe .claude/skills/run-synthetic-data-factory/driver.py --embed --test
```

## Run (human path)

Each module is its own CLI (`python -m app.<mod>.cli`). All verified this session:

```bash
# Parse a file or directory -> DOM store (raw + dom + images)
.venv/Scripts/python.exe -m app.parser.cli --in <file_or_dir> --out <store_dir>
# e.g. .venv/Scripts/python.exe -m app.parser.cli --in _samples --out parser_out
# → "parsed 3/3 documents -> store under parser_out", DOMs at parser_out/dom/*.dom.json

# Normalize one parsed DOM -> clean DOM + provenance report
.venv/Scripts/python.exe -m app.normalizer.cli --dom <store>/dom/<id>.dom.json --out out.norm.json
# → "normalized <id>.dom.json -> out.norm.json"

# Batch parse+normalize a corpus (parallel, incremental via manifest)
.venv/Scripts/python.exe -m app.processing.cli --in <corpus_dir> --out <store> --concurrency 2 --manifest work/manifest.json
# → "parsed+norm : N   skipped: M   failed: K"; re-run skips everything done.
# NB: --manifest must differ between distinct jobs (default work/manifest.json is shared).
```

## Direct invocation (library)

Most PRs touch internals, not the CLIs — import and call directly (cwd = repo root):

```bash
.venv/Scripts/python.exe -c "from app.parser.detection import detect; from app.normalizer.normalizer import Normalizer; print(detect(b'%PDF-1.7 x').slug)"
# → pdf
```

## Test

```bash
.venv/Scripts/python.exe -m pytest
# → 27 passed
```

## Gotchas

- **`--manifest` used to be a no-op.** `app/processing/cli.py` parsed the flag but never
  wired it into `ProcessingConfig`, so every run silently used the default
  `work/manifest.json` — a second job with a *different* `--manifest` still skipped files
  it had never seen. Fixed this session (one `replace(cfg, manifest_path=...)` line).
  Before trusting a `--manifest`, verify `report.manifest_path` matches the flag.
- **The default manifest is shared across jobs.** Don't point two different corpora at the
  same manifest path or the second inherits the first's "done" set. Give each job its own
  `--manifest`.
- **`scripts/` is not a package.** `python -m scripts.check_embedder.py` fails. Run it as
  a script with the repo root on the path: `PYTHONPATH=. python scripts/check_embedder.py`.
- **`check_embedder.py` needs `PYTHONPATH=.`** — as a script its own dir (`scripts/`) is
  `sys.path[0]`, so `from app.embedding import ...` fails without it.
- **`models/bge-m3` is git-ignored.** The GPU embedder silently falls back to
  `DummyEmbedder` if the weights aren't there — no error. After a fresh clone run
  `PYTHONPATH=. python scripts/download_models.py` first, then re-check
  `scripts/check_embedder.py` shows `sentence-transformers` / `cuda`, not a dummy.
- **First embed call is slow** (model load + compile). `check_embedder.py` reports
  ~800 ms for 3 texts after a ~1 s load — don't mistake it for a hang.
- **The parser store is additive and idempotent** (`document_id = sha256(source)`).
  Re-parsing the same bytes rewrites the same keys; it does not clean the store.

## Troubleshooting

- **`python -m scripts.check_embedder.py` → `No module named scripts`**: `scripts/` isn't a
  package. Run `PYTHONPATH=. python scripts/check_embedder.py` instead.
- **`check_embedder.py` → `ModuleNotFoundError: No module named 'app'`**: script run without
  `PYTHONPATH=.`; the repo root isn't on `sys.path`.
- **Batch run says `skipped: N` on a corpus you've never processed**: either the default
  `work/manifest.json` already has those content hashes (it's shared), or you're pointing
  two jobs at one `--manifest`. Use a distinct `--manifest` path.
- **Embedder prints `DummyEmbedder` / `dim: 4` when you expect BGE-M3**: `models/bge-m3`
  is missing (git-ignored). Run `PYTHONPATH=. python scripts/download_models.py`, re-check.
