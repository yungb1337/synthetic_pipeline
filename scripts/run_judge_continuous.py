#!/usr/bin/env python
"""Continuous judge driver: judges parsed DOMs as they arrive until target count reached."""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

JUDGE = Path(__file__).resolve().parent / "llm_judge.py"
METRICS = ("completeness", "fidelity", "structure", "tables", "references", "scans_ocr")

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _find_doms(store: Path) -> list[tuple[str, Path]]:
    out = []
    dom_root = store / "dom"
    if not dom_root.is_dir():
        return out
    for doc_dir in sorted(dom_root.glob("d-*")):
        for dom in sorted(doc_dir.glob("dom-*.docJSON")):
            out.append((doc_dir.name, dom))
            break
    return out

def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--pdf-dir", required=True)
    ap.add_argument("--judgments", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--target", type=int, default=300)
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--pacing", type=float, default=1.5)
    args = ap.parse_args()

    store = Path(args.store)
    pdf_dir = Path(args.pdf_dir)
    judgments = Path(args.judgments)
    judgments.mkdir(parents=True, exist_ok=True)
    out_file = Path(args.out)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Quick lookup in raw/ or scan
    raw_dir = store / "raw"
    print(f"[continuous-judge] Target: {args.target} documents | Model: {args.model}", flush=True)

    while True:
        # Check current valid verdicts
        valid_judgments = []
        for jf in judgments.glob("d-*.json"):
            try:
                rec = json.loads(jf.read_text(encoding="utf-8"))
                if rec.get("verdict"):
                    valid_judgments.append(rec)
            except Exception:
                pass

        print(f"[continuous-judge] Currently valid judged: {len(valid_judgments)} / {args.target}", flush=True)
        if len(valid_judgments) >= args.target:
            print(f"[continuous-judge] Target reached ({len(valid_judgments)} >= {args.target})! Writing aggregate report...", flush=True)
            _write_aggregate_report(valid_judgments[:args.target], out_file, args.model)
            break

        doms = _find_doms(store)
        unjudged = []
        for doc_id, dom_path in doms:
            out_path = judgments / f"{doc_id}.json"
            if not out_path.exists():
                unjudged.append((doc_id, dom_path, out_path))

        if not unjudged:
            print(f"[continuous-judge] No unjudged DOMs found right now ({len(doms)} total DOMs). Waiting 10s for parser...", flush=True)
            time.sleep(10.0)
            continue

        print(f"[continuous-judge] Found {len(unjudged)} unjudged DOMs. Processing batch...", flush=True)
        for doc_id, dom_path, out_path in unjudged:
            # Re-check count
            valid_count = len([j for j in judgments.glob("d-*.json") if j.is_file()])
            if valid_count >= args.target:
                break

            try:
                dom_data = json.loads(dom_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            sha = dom_data.get("source_hash")
            if not sha:
                continue

            # Find PDF in raw/ or pdf_dir
            pdf = raw_dir / f"{sha}.pdf"
            if not pdf.is_file():
                # Fallback to pdf_dir
                matching = list(pdf_dir.glob(f"*{sha[:8]}*.pdf"))
                if matching:
                    pdf = matching[0]
                else:
                    continue

            time.sleep(args.pacing)
            cmd = [
                sys.executable, str(JUDGE),
                "--pdf", str(pdf),
                "--dom", str(dom_path),
                "--out", str(out_path),
                "--model", args.model,
                "--max-chars", "6000"
            ]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
                if r.returncode == 0:
                    try:
                        rec = json.loads(out_path.read_text(encoding="utf-8"))
                        v = rec.get("verdict", {})
                        fid = v.get("metrics", {}).get("fidelity")
                        tab = v.get("metrics", {}).get("tables")
                        print(f"  [judge] {doc_id} -> {v.get('verdict')} (fid={fid}, tab={tab})", flush=True)
                    except Exception:
                        pass
                else:
                    print(f"  [judge-err] {doc_id} rc={r.returncode}", flush=True)
            except Exception as exc:
                print(f"  [judge-exc] {doc_id}: {exc}", flush=True)

def _write_aggregate_report(results: list[dict], out_file: Path, model: str):
    verdict_counts = Counter(r.get("verdict", {}).get("verdict", "?") for r in results)
    metric_tot: dict[str, list[float]] = {}
    for m in METRICS:
        metric_tot[m] = [r["verdict"]["metrics"].get(m, -1)
                         for r in results if isinstance(r.get("verdict", {}).get("metrics"), dict)
                         and m in r["verdict"]["metrics"] and r["verdict"]["metrics"].get(m) is not None]

    severity = Counter(i.get("severity", "?")
                       for r in results for i in r.get("verdict", {}).get("issues", []))
    surfaces = Counter(i.get("surface", "?")
                       for r in results for i in r.get("verdict", {}).get("issues", []))
    examples = sorted(
        [i for r in results if r.get("verdict", {}).get("verdict") == "FAIL"
         for i in r.get("verdict", {}).get("issues", []) if i.get("severity") in ("critical", "major")],
        key=lambda i: i.get("severity", ""))[:8]

    lines = [
        f"# Judge Summary — Full Corpus ({_now()})",
        "",
        f"- Docs judged: **{len(results)}** · model: `{model}`",
        f"- Verdicts: " + ", ".join(f"{k}=`{v}`" for k, v in verdict_counts.items()),
        "",
        "## Metrics means (0..1, across judged docs)",
        "",
        "| metric | mean | n |",
        "|--------|------|---|",
    ]
    for m in METRICS:
        vals = metric_tot[m]
        mean = sum(vals) / len(vals) if vals else float("nan")
        lines.append(f"| {m} | {mean:.3f} | {len(vals)} |")
    lines += [
        "",
        "## Issue tally",
        "",
        f"- By severity: " + ", ".join(f"{k}=`{v}`" for k, v in sorted(severity.items())),
        f"- By surface:  " + ", ".join(f"{k}=`{v}`" for k, v in sorted(surfaces.items())),
        "",
        "## Critical/Major examples (FAIL docs)",
        "",
    ]
    if examples:
        for i in examples:
            lines.append(f"- **[{i.get('severity')} / {i.get('surface')}]** {i.get('detail', '')[:200]}")
    else:
        lines.append("(none in this batch)")

    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"[continuous-judge] Summary written to {out_file}")

if __name__ == "__main__":
    main()
