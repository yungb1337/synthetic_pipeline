"""Parse a single PDF through the production parser and write its canonical DOM.

This is the one-file counterpart of `rerun_test_cases.py`. Useful for re-parsing
a document that dropped a page under heavy-batch memory pressure (intermittent
`std::bad_alloc`/OOM on constrained boxes) — a single, less-loaded run usually
recovers the dropped page(s).

Usage:
    .venv/Scripts/python.exe scripts/parse_one.py \
        --file C:/Users/Asus/Downloads/test_cases/2503.14023v2.pdf \
        --out  C:/Users/Asus/Downloads/test_cases_output

Exit code is 0 on success (DOM written), 2 if the path is missing, and the
parser's own status is reported on stdout. A "failed"/dead doc emits no DOM
(page-centric zero-silent-loss).
"""
from __future__ import annotations

import os
import sys

# `--cpu` MUST be applied before torch / onnxruntime / docling are imported:
# Docling's layout model runs through ONNX Runtime, which otherwise picks the
# CUDA execution provider and OOMs on constrained GPUs. A wrapper like
# `CUDA_VISIBLE_DEVICES=-1` is unreliable in some shells (the background runner
# strips it), so we force it here, inside the process, where nothing can drop it.
if "--cpu" in sys.argv:
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    os.environ["OMP_NUM_THREADS"] = os.environ.get("OMP_NUM_THREADS", "4")

import argparse
import time
from pathlib import Path

# Make `app` importable when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parser.config import default_config
from app.parser.storage import FilesystemStore
from app.parser.extraction import Extractor


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", dest="path", required=True, help="path to a single PDF")
    ap.add_argument("--out", dest="outdir", required=True, help="output root (FilesystemStore)")
    ap.add_argument("--cpu", action="store_true",
                    help="force CPU (hide CUDA) — use when the GPU OOMs during Docling layout")
    args = ap.parse_args()

    src = Path(args.path)
    if not src.is_file():
        print(f"no such file: {src}", file=sys.stderr)
        return 2

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    config = default_config()
    store = FilesystemStore(str(outdir))
    ex = Extractor(config, store)

    data = src.read_bytes()
    t0 = time.time()
    try:
        out = ex.extract(data, src.name)
    except Exception as exc:  # never let one file abort the run
        print(f"EXC  {src.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    elapsed = round((time.time() - t0) * 1000)

    doc = out.document
    blocks = doc.num_blocks() if doc else 0
    tables = doc.num_tables() if doc else 0
    images = doc.num_images() if doc else 0
    refs = len(doc.references) if doc and hasattr(doc, "references") else 0
    expect = out.report.get("expected_pages", 0) if out.report else 0
    actual = out.report.get("pages", out.report.get("actual_pages", 0)) if out.report else 0

    if out.ok:
        dom_key = f"dom/{out.document_id}/dom-v0.1.0.docJSON"
        print(f"OK   {src.name}: doc_id={out.document_id} pages={actual}/{expect} "
              f"blocks={blocks} tables={tables} images={images} refs={refs} "
              f"({elapsed}ms) -> {dom_key}")
        return 0
    print(f"FAIL {src.name}: status={out.status} pages={actual}/{expect} "
          f"({elapsed}ms) {out.report.get('error','') if out.report else ''}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
