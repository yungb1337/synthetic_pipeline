#!/usr/bin/env python3
"""Report near-duplicate functions across Python files - a heuristic signal for
the Quality & Performance Reviewer, not a verdict. Stdlib only.

Method: parse each file with `ast`, extract every function as a "unit" (its
identifier tokens, 3-grams), and rank pairs by Jaccard similarity of their
trigram sets. Identical logic shows up as high overlap even when variable names
differ. Always read a flagged pair before acting on it.

Usage:
    python scripts/check_similarity.py [--paths app] [--min-lines 5]
        [--threshold 0.40] [--top 40]
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

IDENT_RE = re.compile(r"[A-Za-z_]\w*")
STOP_TOKENS = {"self", "cls", "arg", "args", "kwargs", "value", "return"}


def iter_py_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        root = Path(raw)
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(p for p in root.rglob("*.py") if p.is_file())
        else:
            print(f"note: path not found: {raw}", file=sys.stderr)
    return files


def ngrams(tokens: list[str], n: int = 3) -> set[tuple[str, ...]]:
    if len(tokens) < n:
        return set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def collect_units(path: Path, text: str, min_lines: int) -> list[tuple[Path, int, str, int, set]]:
    """Return (path, lineno, name, line_count, trigrams) for each function."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    units: list[tuple[Path, int, str, int, set]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        src = ast.get_source_segment(text, node)
        if src is None:
            continue
        nlines = src.count("\n") + 1
        if nlines < min_lines:
            continue
        ids: list[str] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                ids.append(sub.id)
            elif isinstance(sub, ast.Attribute):
                ids.append(sub.attr)
        tokens = [t.lower() for t in ids if len(t) >= 3 and t.lower() not in STOP_TOKENS]
        grams = ngrams(tokens)
        if grams:
            units.append((path, node.lineno, node.name, nlines, grams))
    return units


def jaccard(a: set, b: set) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paths", nargs="*", default=["app"], help="files/dirs to scan (default: app)")
    ap.add_argument("--min-lines", type=int, default=5, help="ignore functions shorter than this")
    ap.add_argument("--threshold", type=float, default=0.40, help="report pairs with similarity >= this")
    ap.add_argument("--top", type=int, default=40, help="max pairs to print")
    args = ap.parse_args()

    files = iter_py_files(args.paths)
    units: list[tuple[Path, int, str, int, set]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"note: cannot read {path}: {exc}", file=sys.stderr)
            continue
        units.extend(collect_units(path, text, args.min_lines))

    print("code-similarity report (heuristic - read flagged pairs before acting)")
    print(f"paths: {', '.join(args.paths)}  |  min_lines: {args.min_lines}  |  threshold: {args.threshold}")
    print(f"scanned {len(files)} files, {len(units)} function units")

    pairs = []
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            sim = jaccard(units[i][4], units[j][4])
            if sim >= args.threshold:
                pairs.append((sim, units[i], units[j]))
    pairs.sort(key=lambda p: p[0], reverse=True)

    if not pairs:
        print(f"\nno pairs at or above threshold={args.threshold}")
        return 0

    print(f"\n{len(pairs)} pair(s) at or above threshold={args.threshold} (showing top {args.top}):\n")
    for sim, a, b in pairs[: args.top]:
        a_path, a_ln, a_name, a_lines, a_grams = a
        b_path, b_ln, b_name, b_lines, b_grams = b
        overlap = len(a_grams & b_grams)
        print(f"  {sim:0.2f}  {a_path}:{a_ln} {a_name}() [{a_lines}ln]  ~  {b_path}:{b_ln} {b_name}() [{b_lines}ln]")
        print(f"        shared trigrams: {overlap}/{len(a_grams | b_grams)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
