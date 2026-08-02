"""CLI for the parser module (Extraction -> DOM).

Usage:
    python -m app.parser.cli --in FILE|DIR [--out DIR] [--no-ocr]

Processes a file (or every file under a directory), writes raw + DOM + images
to the Store root, and prints a per-document summary + the parsed event.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from .config import default_config
from .events import EventPublisher
from .extraction import Extractor
from .storage import FilesystemStore


def _file_types():
    return (".pdf", ".docx", ".xlsx", ".csv", ".tsv", ".json", ".xml", ".html",
            ".md", ".markdown", ".txt", ".png", ".jpg", ".jpeg", ".tiff", ".gif")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synthetic Data Factory — Parser (Extraction->DOM)")
    ap.add_argument("--in", dest="input", required=True, help="input file or directory")
    ap.add_argument("--out", dest="out", default="parser_out", help="store root dir")
    ap.add_argument("--no-ocr", action="store_true", help="disable OCR")
    args = ap.parse_args(argv)

    cfg = default_config()
    if args.no_ocr:
        cfg = replace(cfg, ocr_enabled=False)
    store = FilesystemStore(args.out)
    extractor = Extractor(cfg, store, events=EventPublisher())

    path = Path(args.input)
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.suffix.lower() in _file_types())

    if not files:
        print("no supported files found", file=sys.stderr)
        return 2

    ok = 0
    for f in files:
        data = f.read_bytes()
        outcome = extractor.extract(data, filename=f.name)
        if outcome.ok:
            ok += 1
            print(f"OK   {f.name:28} {outcome.detected.slug:10} pages={len(outcome.document.pages):<3} "
                  f"blocks={outcome.report['blocks']:<4} tables={outcome.report['tables']:<3} "
                  f"el={outcome.report['elapsed_ms']}ms")
        else:
            print(f"SKIP {f.name:28} {outcome.status}")

    print(f"\nparsed {ok}/{len(files)} documents -> store under {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())