#!/usr/bin/env python
"""Reliable folder parser for the page-centric parser (ADR-013).

Wraps `python -m app.parser.cli` so you can paste an input folder and an output
folder and run. The output folder is created automatically if it does not exist.

Usage (two equivalent styles):
    python scripts/parse_folder.py "C:/path/to/input" "C:/path/to/output"
    python scripts/parse_folder.py --in "C:/path/to/input" --out "C:/path/to/output"

Optional passthrough flags (same as app.parser.cli):
    --no-ocr                 disable OCR
    --native-concurrency N   native pool size (default: auto)
    --heavy-concurrency N    heavy (Docling) pool size (default: RAM-derived)

The script:
  - resolves INPUT (file or directory); errors clearly if missing
  - creates OUTPUT (recursively) before delegating
  - forwards to the real parser; exit code mirrors the parser's result
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _resolve_venv_python() -> str:
    """Return the venv python executable, preferring the active one if it is
    inside a venv, else the repo-local .venv."""
    here = Path(__file__).resolve().parent
    repo = here.parent
    candidates = []
    # 1) currently running interpreter (often already the venv one)
    candidates.append(sys.executable)
    # 2) repo-local venv (Windows then POSIX layouts)
    candidates += [
        repo / ".venv" / "Scripts" / "python.exe",
        repo / ".venv" / "bin" / "python",
    ]
    for c in candidates:
        c = Path(c)
        if c.is_file():
            return str(c)
    # Fall back to whatever `python` resolves to on PATH.
    return "python"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Parse an input folder/file with the page-centric parser; "
                    "auto-creates the output folder.")
    ap.add_argument("input_pos", nargs="?", help="input file or directory (positional)")
    ap.add_argument("output_pos", nargs="?", help="output store dir (positional)")
    ap.add_argument("--in", dest="input", help="input file or directory")
    ap.add_argument("--out", dest="output", help="output store dir")
    ap.add_argument("--no-ocr", action="store_true", help="disable OCR")
    ap.add_argument("--native-concurrency", type=int, default=None)
    ap.add_argument("--heavy-concurrency", type=int, default=None)
    args = ap.parse_args(argv)

    # Accept either positional or --in/--out forms.
    in_path = args.input or args.input_pos
    out_path = args.output or args.output_pos

    if not in_path:
        ap.error("INPUT is required (positional arg or --in)")
    if not out_path:
        out_path = "parser_out"

    src = Path(in_path).expanduser().resolve()
    dst = Path(out_path).expanduser().resolve()

    if not src.exists():
        print(f"ERROR: input does not exist: {src}", file=sys.stderr)
        return 2

    # Auto-create the output folder (recursively) if missing.
    try:
        dst.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"ERROR: cannot create output dir {dst}: {exc}", file=sys.stderr)
        return 2

    print(f"[parse_folder] input : {src}")
    print(f"[parse_folder] output: {dst}  (created)" if not dst.exists() else
          f"[parse_folder] output: {dst}")
    print(f"[parse_folder] launching parser...\n")

    py = _resolve_venv_python()
    cmd = [py, "-m", "app.parser.cli", "--in", str(src), "--out", str(dst)]
    if args.no_ocr:
        cmd.append("--no-ocr")
    if args.native_concurrency is not None:
        cmd += ["--native-concurrency", str(args.native_concurrency)]
    if args.heavy_concurrency is not None:
        cmd += ["--heavy-concurrency", str(args.heavy_concurrency)]

    # Pass through so the user sees the parser's own progress/summary.
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
