#!/usr/bin/env python
"""compare_benchmarks.py — join benchmark-*.md reports into a comparison table.

Usage:
    .venv/Scripts/python.exe scripts/compare_benchmarks.py \
        --reports <reports_dir> --baseline benchmark-b100.md \
        --runs benchmark-postfixA.md benchmark-postfixB.md \
        [--out comparison.md]

Extracts per-batch: wall time, pages, ms/page (=> pages/s), peak tree RSS,
peak worker RSS, OK/failed/dead counts and extraction yield, then prints a
markdown table with delta columns vs the baseline.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path


def parse_report(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    r: dict = {"file": path.name}

    def grab(pattern: str, cast=str):
        m = re.search(pattern, text)
        return cast(m.group(1)) if m else None

    r["issued"] = grab(r"Documents\*\*: (\d+) issued", int)
    r["ok"] = grab(r"`(\d+)` OK", int)
    r["failed"] = grab(r"`(\d+)` failed", int)
    r["dead"] = grab(r"`(\d+)` dead", int)
    r["unparsed"] = grab(r"`(\d+)` unparsed", int)
    r["wall_s"] = grab(r"Total wall time[^`]*`([\d\.]+)s", float)
    r["pages"] = grab(r"Total pages parsed: `(\d+)`", int)
    r["ms_per_page"] = grab(r"Mean time per page: `([\d\.]+)` ms", float)
    r["peak_tree_mb"] = grab(r"Total tree RSS\): `(\d+) MB", float)
    r["peak_worker_mb"] = grab(r"Max single-process RSS\): `(\d+) MB", float)
    r["blocks"] = grab(r"`(\d+)` blocks", int)
    r["tables"] = grab(r"`(\d+)` tables", int)
    r["refs"] = grab(r"`(\d+)` references", int)

    # Derived: pages/s = pages / wall; also cross-check ms/page.
    if r["wall_s"] and r["pages"]:
        r["pages_per_s_wall"] = round(r["pages"] / r["wall_s"], 4)
    else:
        r["pages_per_s_wall"] = None
    if r["ms_per_page"]:
        r["pages_per_s_doc"] = round(1000.0 / r["ms_per_page"], 4)
    else:
        r["pages_per_s_doc"] = None
    return r


def fmt_pct(new: float | None, base: float | None) -> str:
    if not new or not base or base == 0:
        return "-"
    pct = (new - base) / base * 100.0
    return f"{pct:+.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    reports_dir = Path(args.reports)
    base = parse_report(reports_dir / args.baseline)
    runs = [parse_report(reports_dir / r) for r in args.runs]

    lines: list[str] = []
    lines.append("# Benchmark comparison — pre-fix baseline vs post-fix runs\n")
    lines.append(f"Baseline: `{base['file']}`")
    lines.append(f"Runs compared: {', '.join('`' + r['file'] + '`' for r in runs)}\n")

    header = (
        "| metric | baseline (b100, pre-fix) "
        + " ".join(f"| {r['file'].replace('benchmark-', '').replace('.md', '')} (post-fix)" for r in runs)
        + " |"
    )
    sep = "|---|" + "---|" * len(runs)
    lines += [header, sep]

    rows = [
        ("docs OK / issued", lambda r: f"{r['ok']}/{r['issued']}"),
        ("failed/dead/unparsed", lambda r: f"{r['failed']}/{r['dead']}/{r['unparsed']}"),
        ("wall time (min)", lambda r: f"{r['wall_s']/60:.2f}" if r['wall_s'] else "-"),
        ("pages parsed", lambda r: str(r["pages"])),
        ("mean ms/page", lambda r: f"{r['ms_per_page']:.0f}" if r['ms_per_page'] else "-"),
        ("pages/s (per-doc mean)", lambda r: str(r["pages_per_s_doc"])),
        ("pages/s (batch wall)", lambda r: str(r["pages_per_s_wall"])),
        ("peak tree RSS (MB)", lambda r: f"{r['peak_tree_mb']:.0f}" if r['peak_tree_mb'] else "-"),
        ("peak worker RSS (MB)", lambda r: f"{r['peak_worker_mb']:.0f}" if r['peak_worker_mb'] else "-"),
        ("blocks", lambda r: str(r["blocks"])),
        ("tables", lambda r: str(r["tables"])),
        ("references", lambda r: str(r["refs"])),
    ]
    for label, fn in rows:
        cells = " | ".join(fn(r) for r in runs)
        lines.append(f"| {label} | {fn(base)} | {cells} |")

    # Delta lines (vs baseline) for the key metrics.
    lines.append("\n## Deltas vs baseline\n")
    lines.append("| metric | " + " | ".join(
        r["file"].replace("benchmark-", "").replace(".md", "") for r in runs) + " |")
    lines.append(sep)
    for label, key, better in [
        ("pages/s (per-doc mean)", "pages_per_s_doc", "higher"),
        ("pages/s (batch wall)", "pages_per_s_wall", "higher"),
        ("wall time", "wall_s", "lower"),
        ("peak tree RSS", "peak_tree_mb", "lower"),
        ("peak worker RSS", "peak_worker_mb", "lower"),
    ]:
        cells = []
        for r in runs:
            b, n = base.get(key), r.get(key)
            if b is None or n is None:
                cells.append("-")
            else:
                sign = "" if better == "higher" else ""
                pct = (n - b) / b * 100.0 if b else 0.0
                cells.append(f"{pct:+.1f}%")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    out_text = "\n".join(lines) + "\n"
    print(out_text)
    if args.out:
        Path(args.out).write_text(out_text, encoding="utf-8")
        print(f"[compare] written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
