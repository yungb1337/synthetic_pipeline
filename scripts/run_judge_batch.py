#!/usr/bin/env python
"""run_judge_batch.py — judge every parsed DOM against its source PDF, aggregate.

Walks a page-centric parsed store (ADR-013), finds the matching source PDF for
each DOM (via source_hash), runs scripts/llm_judge.py per document, and aggregates
the per-doc verdict JSONs into a summary. This is the "accuracy" leg of the
parser-reliability campaign: the LLM judge scores source-vs-DOM correspondence on
multiple metrics (completeness, fidelity, structure, tables, references, scans_ocr).

Usage:
    .venv/Scripts/python.exe scripts/run_judge_batch.py \
        --store <parsed> --pdf-dir <sources/pdf> \
        --judgments <run>/judgment --out <run>/reports/judge-summary-<batch>.md \
        [--batch b01] [--limit N] [--force] [--model gemini-3.5-flash-lite]

Skips docs that already have a judgment file (unless --force). If no API key
resolves, prints a clear notice and exits 3 (judge is additive, never fatal).

Writes:
  <judgments>/<doc_id>.json    raw per-doc verdict record
  <out>                        aggregate summary (metric means, verdict counts, issue tally)
  <judgments>/skipped.json     append-only log of skipped/unresolved docs
Dependencies: PyMuPDF + google.generativeai via scripts/llm_judge.py."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

JUDGE = Path(__file__).resolve().parent / "llm_judge.py"
METRICS = ("completeness", "fidelity", "structure", "tables", "references", "scans_ocr")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_doms(store: Path) -> list[tuple[str, Path]]:
    """Return [(doc_id, dom_file)] for every stored DOM document."""
    out: list[tuple[str, Path]] = []
    dom_root = store / "dom"
    if not dom_root.is_dir():
        return out
    for doc_dir in sorted(dom_root.glob("d-*")):
        for dom in sorted(doc_dir.glob("dom-*.docJSON")):
            out.append((doc_dir.name, dom))
            break  # one canonical DOM per doc_id
    return out


def _read_dom_id(dom_file: Path) -> tuple[str, dict | None]:
    try:
        return dom_file.parent.name, json.loads(dom_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return dom_file.parent.name, None


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", required=True, help="parsed store root (dom/ raw/)")
    ap.add_argument(
        "--pdf-dir", required=True, help="dir of source PDFs (source recovery)"
    )
    ap.add_argument(
        "--judgments", required=True, help="dir to write <doc_id>.json verdicts"
    )
    ap.add_argument("--out", required=True, help="aggregate summary markdown path")
    ap.add_argument("--batch", default="b01", help="batch label for the summary")
    ap.add_argument(
        "--limit", type=int, default=0, help="max docs judged this run (0=all)"
    )
    ap.add_argument(
        "--force", action="store_true", help="re-judge docs that already have verdicts"
    )
    ap.add_argument(
        "--model", default="gemini-3.5-flash-lite", help="light Gemini model"
    )
    ap.add_argument(
        "--max-chars", type=int, default=6000, help="source text char budget per doc"
    )
    ap.add_argument(
        "--pacing",
        type=float,
        default=4.0,
        help="seconds to sleep between docs (free-tier quota guard)",
    )
    args = ap.parse_args()

    store = Path(args.store)
    pdf_dir = Path(args.pdf_dir)
    judgments = Path(args.judgments)
    judgments.mkdir(parents=True, exist_ok=True)

    doms = _find_doms(store)
    if not doms:
        print(f"ERROR: no DOM docs found under {store / 'dom'}", file=sys.stderr)
        return 2
    print(f"[judge] {args.batch}: {len(doms)} DOM docs in store; model={args.model}")

    # Build sha -> pdf path map once (cheap for localized corpora)
    sha_index: dict[str, Path] = {}
    if pdf_dir.is_dir():
        for p in pdf_dir.glob("*.pdf"):
            s = _sha256(p)
            if s:
                sha_index[s] = p
    if store.is_dir():
        for p in (store / "raw").glob("*.pdf"):
            s = _sha256(p)
            if s:
                sha_index.setdefault(s, p)

    todo = doms
    if args.limit > 0:
        todo = todo[: args.limit]

    done = skipped = unresolved = 0
    results: list[dict] = []
    skipped_log: list[dict] = []

    for doc_id, dom_file in todo:
        out_path = judgments / f"{doc_id}.json"
        if out_path.exists() and not args.force:
            try:
                rec = json.loads(out_path.read_text(encoding="utf-8"))
                if rec.get("verdict"):
                    results.append(rec)
                    done += 1
                    continue
            except Exception:  # noqa: BLE001 — corrupt verdict, re-judge
                pass

        _, dom = _read_dom_id(dom_file)
        if dom is None:
            skipped += 1
            skipped_log.append({"doc_id": doc_id, "reason": "dom-unreadable"})
            continue

        sha = dom.get("source_hash")
        pdf = sha_index.get(sha) if sha else None
        if pdf is None:
            skipped += 1
            unresolved += 1
            skipped_log.append(
                {"doc_id": doc_id, "reason": "no-source-pdf", "source_hash": sha}
            )
            continue

        if args.pacing > 0:
            time.sleep(args.pacing)  # free-tier quota guard between API calls

        cmd = [
            sys.executable,
            str(JUDGE),
            "--pdf",
            str(pdf),
            "--dom",
            str(dom_file),
            "--out",
            str(out_path),
            "--model",
            args.model,
            "--max-chars",
            str(args.max_chars),
        ]
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            skipped += 1
            skipped_log.append({"doc_id": doc_id, "reason": "timeout"})
            print(f"  TIMEOUT {doc_id}", file=sys.stderr)
            continue

        if r.returncode == 3:  # key missing / auth fatal — stop, nothing verifiable
            print(
                f"ERROR: judge exited 3 (API key/auth fatal) at {doc_id}; "
                f"set GEMINI_API_KEY or key.py. Aborting batch.",
                file=sys.stderr,
            )
            if r.stderr:
                print(r.stderr.strip(), file=sys.stderr)
            _write_skipped(judgments, skipped_log)
            return 3
        if r.returncode == 4:  # rate-limited after internal retries — skip, continue
            skipped += 1
            skipped_log.append({"doc_id": doc_id, "reason": "rate-limited"})
            print(
                f"  SKIP {doc_id} rate-limited (retries exhausted) {(r.stderr or '')[-200:]}",
                file=sys.stderr,
            )
            continue
        if r.returncode != 0:
            skipped += 1
            skipped_log.append(
                {
                    "doc_id": doc_id,
                    "reason": f"judge-exit-{r.returncode}",
                    "stderr": (r.stderr or r.stdout or "")[-300:],
                }
            )
            print(
                f"  SKIP {doc_id} exit={r.returncode} {(r.stderr or '')[-200:]}",
                file=sys.stderr,
            )
            continue

        try:
            rec = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            skipped += 1
            skipped_log.append({"doc_id": doc_id, "reason": "verdict-unreadable"})
            continue
        results.append(rec)
        done += 1
        v = rec.get("verdict", {})
        print(
            f"  judged {doc_id} -> {v.get('verdict')} "
            f"(fid={v.get('metrics', {}).get('fidelity')})"
        )

    _write_skipped(judgments, skipped_log)

    # Aggregate
    verdict_counts = Counter(r.get("verdict", {}).get("verdict", "?") for r in results)
    metric_tot: dict[str, list[float]] = {}
    for m in METRICS:
        metric_tot[m] = [
            r["verdict"]["metrics"].get(m, -1)
            for r in results
            if isinstance(r.get("verdict", {}).get("metrics"), dict)
            and m in r["verdict"]["metrics"]
        ]
    severity = Counter(
        i.get("severity", "?")
        for r in results
        for i in r.get("verdict", {}).get("issues", [])
    )
    surfaces = Counter(
        i.get("surface", "?")
        for r in results
        for i in r.get("verdict", {}).get("issues", [])
    )
    examples = sorted(
        [
            i
            for r in results
            if r.get("verdict", {}).get("verdict") == "FAIL"
            for i in r.get("verdict", {}).get("issues", [])
            if i.get("severity") in ("critical", "major")
        ],
        key=lambda i: i.get("severity", ""),
    )[:8]

    lines = [
        f"# Judge Summary — {args.batch} ({_now()})",
        "",
        f"- Docs judged: **{done}** · skipped: `{skipped}` · unresolved-source: `{unresolved}` "
        f"· model: `{args.model}`",
        "- Verdicts: " + ", ".join(f"{k}=`{v}`" for k, v in verdict_counts.items()),
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
        "- By severity: "
        + ", ".join(f"{k}=`{v}`" for k, v in sorted(severity.items())),
        "- By surface:  "
        + ", ".join(f"{k}=`{v}`" for k, v in sorted(surfaces.items())),
        "",
        "## Critical/Major examples (FAIL docs)",
        "",
    ]
    if examples:
        for i in examples:
            lines.append(
                f"- **[{i.get('severity')} / {i.get('surface')}]** {i.get('detail', '')[:200]}"
            )
    else:
        lines.append("(none in this batch)")

    out = Path(args.out)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"[judge] batch {args.batch} done: judged={done} skipped={skipped} "
        f"-> {verdict_counts}"
    )
    print(f"[judge] summary: {out}")
    return 0


def _write_skipped(judgments: Path, skipped_log: list[dict]) -> None:
    if not skipped_log:
        return
    try:
        sp = judgments / "skipped.jsonl"
        with sp.open("a", encoding="utf-8") as f:
            for rec in skipped_log:
                f.write(json.dumps({**rec, "at": _now()}) + "\n")
    except OSError as exc:
        print(f"  [judge] cannot write skipped log: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
