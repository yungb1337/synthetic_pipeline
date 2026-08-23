"""Rerun every file in a test_cases directory through the production parser and
write each canonical DOM to the existing `test_cases_output/dom/<doc_id>/dom-v0.1.0.docJSON`
location, so the output can be diffed against the source PDFs (and against any
prior run's DOMs captured elsewhere).

Uses the DEFAULT parser config (layout_backend="auto", docling_table_mode="FAST"),
which is exactly the path the structural-extraction fixes target. A "failed"/dead
doc emits no DOM (page-centric zero-silent-loss), so the manifest records status
per file.

Usage:
    .venv/Scripts/python.exe scripts/rerun_test_cases.py \
        --in  C:/Users/Asus/Downloads/test_cases \
        --out C:/Users/Asus/Downloads/test_cases_output \
        --manifest C:/Users/Asus/Downloads/test_cases_output/rerun_manifest.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

# Make `app` importable when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parser.config import default_config
from app.parser.storage import FilesystemStore
from app.parser.extraction import Extractor


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="indir", required=True)
    ap.add_argument("--out", dest="outdir", required=True)
    ap.add_argument("--manifest", dest="manifest", default=None)
    args = ap.parse_args()

    indir = Path(args.indir)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in indir.iterdir() if p.is_file())
    if not files:
        print(f"no files in {indir}", file=sys.stderr)
        return 2

    config = default_config()
    store = FilesystemStore(str(outdir))
    ex = Extractor(config, store)

    rows = []
    ok = 0
    for f in files:
        data = f.read_bytes()
        t0 = time.time()
        try:
            out = ex.extract(data, f.name)
        except Exception as exc:  # never let one file abort the batch
            print(f"EXC  {f.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            rows.append((f.name, "exception", "", 0, 0, 0, 0, 0, f"{type(exc).__name__}: {exc}"))
            continue
        elapsed = round((time.time() - t0) * 1000)
        doc = out.document
        blocks = doc.num_blocks() if doc else 0
        tables = doc.num_tables() if doc else 0
        images = doc.num_images() if doc else 0
        refs = len(doc.references) if doc and hasattr(doc, "references") else 0
        expect = out.report.get("expected_pages", 0) if out.report else 0
        actual = out.report.get("actual_pages", 0) if out.report else 0
        if out.ok:
            ok += 1
            print(f"OK   {f.name}: doc_id={out.document_id} pages={actual}/{expect} "
                  f"blocks={blocks} tables={tables} images={images} refs={refs} ({elapsed}ms)")
        else:
            print(f"FAIL {f.name}: status={out.status} pages={actual}/{expect} "
                  f"({elapsed}ms) {out.report.get('error','') if out.report else ''}")
        rows.append((f.name, out.status, out.document_id or "", expect, actual,
                     blocks, tables, images, refs))

    # Manifest
    manifest = args.manifest or str(outdir / "rerun_manifest.csv")
    with open(manifest, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "status", "document_id", "expected_pages", "actual_pages",
                    "blocks", "tables", "images", "references"])
        w.writerows(rows)

    print(f"\n=== {ok}/{len(files)} parsed; manifest -> {manifest} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
