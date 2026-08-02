"""CLI for the normalizer module.

Usage:
    python -m app.normalizer.cli --dom <parsed.dom.json> --out <normalized.dom.json>

Reads a parsed DOM (JSON from the Parser module), normalizes block text, writes
a normalized DOM with a normalization report attached to provenance.
"""
from __future__ import annotations

import argparse
import sys

from app.parser.dom import Document
from .config import NormalizerConfig
from .normalizer import Normalizer


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synthetic Data Factory — Normalizer (DOM -> clean DOM)")
    ap.add_argument("--dom", required=True, help="path to a parsed DOM JSON file")
    ap.add_argument("--out", required=True, help="output normalized DOM JSON path")
    args = ap.parse_args(argv)

    doc = Document.model_validate_json(open(args.dom, encoding="utf-8").read())
    normal = Normalizer(NormalizerConfig()).normalize(doc)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(normal.model_dump_json(indent=2))

    report = normal.provenance.normalization_report or {}
    print(f"normalized {args.dom} -> {args.out}")
    print(f"  blocks changed : {report.get('blocks_changed')}/{report.get('blocks_seen')}")
    print(f"  chars in/out   : {report.get('chars_in')} / {report.get('chars_out')}")
    print(f"  report         : {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())