#!/usr/bin/env python
"""run_stratified_judge.py — stratified 120-document LLM judge audit.

Selects a balanced stratified sample of 120 documents (24 per stratum across S1..S5)
from the curated corpus, matches each against its canonical DOM in parsed-b03..b07,
runs `scripts/llm_judge.py` with `gemini-3.5-flash-lite`, and aggregates multi-metric
correspondence scores into a comprehensive audit report.

Strata:
  - S1: Dense tables & multi-table clinical studies (24 docs)
  - S2: Multi-column layouts & complex typography (24 docs)
  - S3: OCR / Scans / legacy academic literature (24 docs)
  - S4: Long documents (>30 pages) / clinical guidelines (24 docs)
  - S5: Clinical trial reports / structured trial outcomes (24 docs)

Usage:
    .venv/Scripts/python.exe scripts/run_stratified_judge.py \
        --manifest checkpoints/run/run-2026-09-04-parser-reliability/sources/manifest.json \
        --pdf-dir checkpoints/run/run-2026-09-04-parser-reliability/sources/pdf \
        --run-dir checkpoints/run/run-2026-09-04-parser-reliability \
        --judgments checkpoints/run/run-2026-09-04-parser-reliability/judgment \
        --out checkpoints/run/run-2026-09-04-parser-reliability/reports/judge-stratified-120.md \
        --sample-per-stratum 24 \
        --model gemini-3.5-flash-lite \
        --pacing 3.5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JUDGE_SCRIPT = REPO_ROOT / "scripts" / "llm_judge.py"
METRICS = ("completeness", "fidelity", "structure", "tables", "references", "scans_ocr")

STRATUM_NAMES = {
    "S1": "Dense Tables & Multi-Table Studies",
    "S2": "Multi-Column Layouts & Typography",
    "S3": "OCR / Scans & Legacy Literature",
    "S4": "Long Documents (>30 pages) & Guidelines",
    "S5": "Clinical Trial Reports & Structured Outcomes",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_python() -> str:
    venv_py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable


def _index_all_doms(run_dir: Path) -> dict[str, tuple[str, Path]]:
    """Returns mapping sha256 -> (doc_id, dom_path)."""
    mapping: dict[str, tuple[str, Path]] = {}
    for batch_dir in sorted(run_dir.glob("parsed-b*")):
        manifest_dir = batch_dir / "manifest"
        dom_dir = batch_dir / "dom"
        if not manifest_dir.is_dir() or not dom_dir.is_dir():
            continue
        for plan_file in manifest_dir.glob("*/plan.json"):
            try:
                plan = json.loads(plan_file.read_text(encoding="utf-8"))
                sh = plan.get("source_hash")
                doc_id = plan.get("doc_id")
                if not sh or not doc_id:
                    continue
                # Find dom file
                doc_dom_dir = dom_dir / doc_id
                if not doc_dom_dir.is_dir():
                    continue
                dom_files = list(doc_dom_dir.glob("dom-*.docJSON"))
                if dom_files:
                    mapping[sh] = (doc_id, dom_files[0])
            except Exception:
                continue
    return mapping


def _select_stratified_sample(
    manifest_entries: list[dict],
    dom_mapping: dict[str, tuple[str, Path]],
    pdf_dir: Path,
    sample_per_stratum: int = 24,
) -> list[dict]:
    """Selects deterministic sample of documents per stratum that have available PDFs and DOMs."""
    by_stratum: dict[str, list[dict]] = defaultdict(list)
    for entry in manifest_entries:
        stratum = entry.get("stratum", "S1")
        sha = entry.get("sha256")
        pdf_id = entry.get("id")
        if not sha or not pdf_id:
            continue
        pdf_path = pdf_dir / f"{pdf_id}.pdf"
        if not pdf_path.is_file():
            continue
        if sha not in dom_mapping:
            continue
        doc_id, dom_path = dom_mapping[sha]
        by_stratum[stratum].append({
            "id": pdf_id,
            "stratum": stratum,
            "sha256": sha,
            "title": entry.get("title", ""),
            "pdf_path": pdf_path,
            "doc_id": doc_id,
            "dom_path": dom_path,
        })

    selected: list[dict] = []
    for stratum in sorted(STRATUM_NAMES.keys()):
        pool = by_stratum.get(stratum, [])
        if not pool:
            continue
        # Deterministic step sampling across the pool to maximize diversity
        n = min(len(pool), sample_per_stratum)
        if n == len(pool):
            sample = list(pool)
        else:
            step = len(pool) / n
            sample = [pool[int(i * step)] for i in range(n)]
        selected.extend(sample)
    return selected


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, help="path to manifest.json")
    ap.add_argument("--pdf-dir", required=True, help="dir of source PDFs")
    ap.add_argument("--run-dir", required=True, help="run dir containing parsed-b* stores")
    ap.add_argument("--judgments", required=True, help="dir to write <doc_id>.json verdicts")
    ap.add_argument("--out", required=True, help="output markdown summary report path")
    ap.add_argument("--sample-per-stratum", type=int, default=24, help="target docs per stratum")
    ap.add_argument("--model", default="gemini-3.5-flash-lite", help="light Gemini model")
    ap.add_argument("--pacing", type=float, default=3.5, help="seconds between calls")
    ap.add_argument("--force", action="store_true", help="re-judge existing verdicts")
    ap.add_argument("--max-chars", type=int, default=6000, help="char budget per doc")
    args = ap.parse_args()

    manifest_path = Path(args.manifest)
    pdf_dir = Path(args.pdf_dir)
    run_dir = Path(args.run_dir)
    judgments_dir = Path(args.judgments)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    judgments_dir.mkdir(parents=True, exist_ok=True)

    manifest_entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    dom_mapping = _index_all_doms(run_dir)
    print(f"[stratified-judge] Indexed {len(dom_mapping)} DOMs across all wave stores.")

    sample = _select_stratified_sample(manifest_entries, dom_mapping, pdf_dir, args.sample_per_stratum)
    print(f"[stratified-judge] Selected {len(sample)} documents across {len(STRATUM_NAMES)} strata.")

    counts_by_s = Counter(item["stratum"] for item in sample)
    for s, c in sorted(counts_by_s.items()):
        print(f"  {s} ({STRATUM_NAMES.get(s, s)}): {c} docs")

    py_exe = _resolve_python()
    results: list[dict] = []
    skipped = 0
    new_judged = 0

    for idx, item in enumerate(sample, start=1):
        doc_id = item["doc_id"]
        pdf_p = item["pdf_path"]
        dom_p = item["dom_path"]
        stratum = item["stratum"]
        vpath = judgments_dir / f"{doc_id}.json"

        # Check existing judgment
        if vpath.is_file() and not args.force:
            try:
                rec = json.loads(vpath.read_text(encoding="utf-8"))
                if rec.get("verdict", {}).get("verdict"):
                    rec["_stratum"] = stratum
                    rec["_pdf_id"] = item["id"]
                    results.append(rec)
                    skipped += 1
                    print(f"[{idx}/{len(sample)}] [{stratum}] {item['id']} ({doc_id}) -> cached {rec['verdict']['verdict']}")
                    continue
            except Exception:
                pass

        cmd = [
            py_exe, str(JUDGE_SCRIPT),
            "--pdf", str(pdf_p),
            "--dom", str(dom_p),
            "--out", str(vpath),
            "--model", args.model,
            "--max-chars", str(args.max_chars),
        ]

        t0 = time.monotonic()
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace", timeout=300)
        dt = time.monotonic() - t0

        if proc.returncode == 3:
            print(f"ERROR: judge API key auth fatal at {doc_id}; aborting.", file=sys.stderr)
            break
        elif proc.returncode == 4:
            print(f"[{idx}/{len(sample)}] [{stratum}] {item['id']} ({doc_id}) -> rate limited (skipped)")
            continue
        elif proc.returncode != 0:
            print(f"[{idx}/{len(sample)}] [{stratum}] {item['id']} ({doc_id}) -> exit {proc.returncode}: {proc.stderr.strip()[:160]}")
            continue

        try:
            rec = json.loads(vpath.read_text(encoding="utf-8"))
            rec["_stratum"] = stratum
            rec["_pdf_id"] = item["id"]
            results.append(rec)
            new_judged += 1
            vd = rec.get("verdict", {})
            vlabel = vd.get("verdict", "UNKNOWN")
            m = vd.get("metrics", {})
            print(f"[{idx}/{len(sample)}] [{stratum}] {item['id']} ({doc_id}) in {dt:.1f}s -> {vlabel} "
                  f"(comp={m.get('completeness', 0):.2f} fid={m.get('fidelity', 0):.2f} struct={m.get('structure', 0):.2f} "
                  f"tbl={m.get('tables', 0):.2f} ref={m.get('references', 0):.2f} ocr={m.get('scans_ocr', 0):.2f})")
        except Exception as e:
            print(f"[{idx}/{len(sample)}] [{stratum}] {item['id']} ({doc_id}) -> corrupt verdict ({e})")

        if args.pacing > 0:
            time.sleep(args.pacing)

    # --- Generate Comprehensive Markdown Report ---
    report_lines = [
        f"# Stratified LLM Judge Audit — 120-Document Benchmark",
        f"",
        f"- Generated: `{_now()}`",
        f"- Target sample: `120` documents (`24` per stratum across `S1..S5`)",
        f"- Evaluated: `{len(results)}` documents (newly judged: `{new_judged}`, cached: `{skipped}`)",
        f"- Judge Model: `{args.model}`",
        f"- Evaluation Harness: Multi-metric source-vs-DOM correspondence (`scripts/llm_judge.py`)",
        f"",
        f"## 1. Executive Summary & Verdict Distribution",
        f"",
    ]

    verdict_counts = Counter(r.get("verdict", {}).get("verdict", "UNKNOWN") for r in results)
    pass_cnt = verdict_counts.get("PASS", 0)
    pwi_cnt = verdict_counts.get("PASS_WITH_ISSUES", 0)
    fail_cnt = verdict_counts.get("FAIL", 0)
    total_judged = len(results)
    pass_rate = (pass_cnt + pwi_cnt) / max(total_judged, 1) * 100.0

    report_lines.extend([
        f"| Verdict | Count | Share | Description |",
        f"|---|---|---|---|",
        f"| **PASS** | `{pass_cnt}` | {pass_cnt/max(total_judged, 1)*100.0:.1f}% | Complete, high-fidelity DOM extraction with zero critical/major defects |",
        f"| **PASS_WITH_ISSUES** | `{pwi_cnt}` | {pwi_cnt/max(total_judged, 1)*100.0:.1f}% | High-fidelity extraction with minor structural / formatting nuances |",
        f"| **FAIL** | `{fail_cnt}` | {fail_cnt/max(total_judged, 1)*100.0:.1f}% | Major extraction defect / substantial content loss |",
        f"| **Total** | `{total_judged}` | 100.0% | **Acceptance Rate (PASS + PASS_WITH_ISSUES): {pass_rate:.1f}%** |",
        f"",
        f"## 2. Multi-Metric Accuracy by Risk Stratum",
        f"",
        f"| Stratum | Description | Docs | Completeness | Fidelity | Structure | Tables | References | Scans/OCR | Verdict (P / PWI / F) |",
        f"|---|---|---|---|---|---|---|---|---|---|",
    ])

    by_s_results: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_s_results[r.get("_stratum", "S1")].append(r)

    for stratum in sorted(STRATUM_NAMES.keys()):
        s_docs = by_s_results.get(stratum, [])
        s_name = STRATUM_NAMES.get(stratum, stratum)
        if not s_docs:
            report_lines.append(f"| `{stratum}` | {s_name} | `0` | - | - | - | - | - | - | 0 / 0 / 0 |")
            continue

        m_avgs = {}
        for m in METRICS:
            vals = [r.get("verdict", {}).get("metrics", {}).get(m) for r in s_docs]
            valid_vals = [v for v in vals if v is not None]
            m_avgs[m] = sum(valid_vals) / len(valid_vals) if valid_vals else 0.0

        v_s = Counter(r.get("verdict", {}).get("verdict", "UNKNOWN") for r in s_docs)
        p_str = f"{v_s.get('PASS', 0)} / {v_s.get('PASS_WITH_ISSUES', 0)} / {v_s.get('FAIL', 0)}"
        report_lines.append(
            f"| `{stratum}` | {s_name} | `{len(s_docs)}` | "
            f"`{m_avgs['completeness']:.3f}` | `{m_avgs['fidelity']:.3f}` | `{m_avgs['structure']:.3f}` | "
            f"`{m_avgs['tables']:.3f}` | `{m_avgs['references']:.3f}` | `{m_avgs['scans_ocr']:.3f}` | {p_str} |"
        )

    # Overall Means
    all_avgs = {}
    for m in METRICS:
        vals = [r.get("verdict", {}).get("metrics", {}).get(m) for r in results]
        valid_vals = [v for v in vals if v is not None]
        all_avgs[m] = sum(valid_vals) / len(valid_vals) if valid_vals else 0.0

    report_lines.extend([
        f"| **Overall** | **Full Stratified Corpus** | `{total_judged}` | "
        f"**`{all_avgs['completeness']:.3f}`** | **`{all_avgs['fidelity']:.3f}`** | **`{all_avgs['structure']:.3f}`** | "
        f"**`{all_avgs['tables']:.3f}`** | **`{all_avgs['references']:.3f}`** | **`{all_avgs['scans_ocr']:.3f}`** | "
        f"**{pass_cnt} / {pwi_cnt} / {fail_cnt}** |",
        f"",
        f"## 3. Issues & Nuances Tally",
        f"",
    ])

    issue_counts: Counter = Counter()
    issues_by_surface: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        doc_id = r.get("doc_id", "")
        pdf_id = r.get("_pdf_id", "")
        stratum = r.get("_stratum", "")
        for iss in r.get("verdict", {}).get("issues", []):
            sev = iss.get("severity", "minor")
            surf = iss.get("surface", "other")
            issue_counts[sev] += 1
            issues_by_surface[surf].append({
                "doc_id": doc_id,
                "pdf_id": pdf_id,
                "stratum": stratum,
                "severity": sev,
                "detail": iss.get("detail", ""),
                "suggestion": iss.get("suggestion", ""),
            })

    report_lines.extend([
        f"- **Total Issues Identified:** `{sum(issue_counts.values())}`",
        f"  - Critical: `{issue_counts.get('critical', 0)}`",
        f"  - Major: `{issue_counts.get('major', 0)}`",
        f"  - Minor: `{issue_counts.get('minor', 0)}`",
        f"",
        f"### Issues by Surface",
        f"",
        f"| Surface | Total Issues | Critical | Major | Minor | Sample Feedback / Suggestion |",
        f"|---|---|---|---|---|---|",
    ])

    for surf, items in sorted(issues_by_surface.items()):
        c_sev = Counter(it["severity"] for it in items)
        sample_sug = items[0]["suggestion"] if items and items[0].get("suggestion") else "—"
        report_lines.append(
            f"| `{surf}` | `{len(items)}` | `{c_sev.get('critical', 0)}` | `{c_sev.get('major', 0)}` | `{c_sev.get('minor', 0)}` | {sample_sug[:80]}... |"
        )

    report_lines.extend([
        f"",
        f"## 4. Per-Document Audit Log (Stratified Sample)",
        f"",
        f"| # | Doc ID | PMC ID | Stratum | Verdict | Completeness | Fidelity | Structure | Tables | Refs | Notes |",
        f"|---|---|---|---|---|---|---|---|---|---|---|",
    ])

    for idx, r in enumerate(results, start=1):
        doc_id = r.get("doc_id", "")
        pdf_id = r.get("_pdf_id", "")
        stratum = r.get("_stratum", "")
        vd = r.get("verdict", {})
        vlabel = vd.get("verdict", "UNKNOWN")
        m = vd.get("metrics", {})
        notes = (vd.get("notes", "") or "").replace("\n", " ")
        if len(notes) > 90:
            notes = notes[:87] + "..."
        report_lines.append(
            f"| {idx} | `{doc_id}` | `{pdf_id}` | `{stratum}` | `{vlabel}` | "
            f"{m.get('completeness', 0):.2f} | {m.get('fidelity', 0):.2f} | {m.get('structure', 0):.2f} | "
            f"{m.get('tables', 0):.2f} | {m.get('references', 0):.2f} | {notes} |"
        )

    out_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"\n[stratified-judge] Audit complete: {len(results)} judged -> {verdict_counts}")
    print(f"[stratified-judge] Report written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
