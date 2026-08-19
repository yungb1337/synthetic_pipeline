#!/usr/bin/env python
"""Compare two parser output stores document-by-document.

Usage:
    python scripts/compare_runs.py <store_a> <store_b> [--names A B]

For every document present (by doc_id) in either store, prints:
  - doc_id
  - present? in A / in B
  - expected_pages / actual_pages in each
  - assembly.status in each
  - DOM checksum in each (so we can see if the recovered DOM is byte-identical)

doc_id is derived from the file content hash (d-<sha256[:16]>), so the SAME
input file yields the SAME doc_id whether run alone or in a queue — making the
two stores directly comparable per document.

Exit code: 0 if every document that appears in both stores is identical in
(expected, actual, status, dom_checksum); otherwise 1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def _dom_checksum(store_root: Path, doc_id: str) -> str | None:
    dom_dir = store_root / "dom" / doc_id
    if not dom_dir.is_dir():
        return None
    files = sorted(dom_dir.glob("dom-v*.docJSON"))
    if not files:
        return None
    h = hashlib.sha256()
    for f in files:
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _ledger_summary(store_root: Path, doc_id: str) -> dict | None:
    lp = store_root / "manifest" / doc_id / "plan.json"
    if not lp.is_file():
        return None
    try:
        L = json.loads(lp.read_text(encoding="utf-8"))
    except Exception:
        return None
    asm = L.get("assembly", {}) or {}
    rep = asm.get("report", {}) or {}
    return {
        "status": asm.get("status"),
        "expected": rep.get("expected_pages"),
        "actual": rep.get("actual_pages"),
        "missing": rep.get("missing_pages"),
        "failed": rep.get("failed_pages"),
        "dead": rep.get("dead_pages"),
    }


def _doc_ids(store_root: Path) -> set[str]:
    manifest = store_root / "manifest"
    if not manifest.is_dir():
        return set()
    return {p.name for p in manifest.iterdir() if (p / "plan.json").is_file()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compare two parser output stores.")
    ap.add_argument("store_a")
    ap.add_argument("store_b")
    ap.add_argument("--names", nargs=2, default=["A", "B"], help="labels for A and B")
    args = ap.parse_args(argv)

    ra, rb = Path(args.store_a), Path(args.store_b)
    na, nb = args.names
    ids_a = _doc_ids(ra)
    ids_b = _doc_ids(rb)
    all_ids = sorted(ids_a | ids_b)

    print(f"Store {na}: {ra}")
    print(f"Store {nb}: {rb}")
    print(f"docs in {na}={len(ids_a)}  docs in {nb}={len(ids_b)}  total distinct={len(all_ids)}\n")

    header = (f"{'doc_id':22} | {'in':5} | "
              f"{na}:stat/exp/act | {nb}:stat/exp/act | DOM-match")
    print(header)
    print("-" * len(header))

    mismatches = 0
    identical = 0
    for d in all_ids:
        ina = d in ids_a
        inb = d in ids_b
        sa = _ledger_summary(ra, d) if ina else None
        sb = _ledger_summary(rb, d) if inb else None
        ca = _dom_checksum(ra, d)
        cb = _dom_checksum(rb, d)

        def fmt(s):
            if s is None:
                return "   -/-/-  "
            return f"{str(s['status']):>4}/{s['expected']}/{s['actual']}"

        inboth = ina and inb
        sa_sb_same = (sa and sb and
                      sa["status"] == sb["status"] and
                      sa["expected"] == sb["expected"] and
                      sa["actual"] == sb["actual"])
        dom_match = (ca == cb) if (ca is not None and cb is not None) else None
        dom_str = {True: "yes", False: "NO ", None: "n/a"}[dom_match]

        flag = ""
        if inboth:
            if sa_sb_same and dom_match:
                identical += 1
            else:
                mismatches += 1
                flag = "  <-- MISMATCH"
        else:
            flag = f"  <-- only in {na if ina else nb}"

        in_str = ("A" if ina else "-") + ("B" if inb else "-")
        print(f"{d:22} | {in_str:5} | {fmt(sa):>12} | {fmt(sb):>12} | {dom_str}{flag}")

    print("-" * len(header))
    print(f"identical (in both + same ledger + same DOM checksum): {identical}")
    print(f"mismatches (in both, differ): {mismatches}")
    only_a = ids_a - ids_b
    only_b = ids_b - ids_a
    if only_a:
        print(f"only in {na}: {sorted(only_a)}")
    if only_b:
        print(f"only in {nb}: {sorted(only_b)}")

    if mismatches > 0:
        print("\nRESULT: NOT IDENTICAL")
        return 1
    if only_a or only_b:
        print("\nRESULT: doc-set differs between stores (see 'only in' above)")
        return 1
    print("\nRESULT: IDENTICAL across both runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
