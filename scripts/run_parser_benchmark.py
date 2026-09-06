#!/usr/bin/env python
"""run_parser_benchmark.py — parse a corpus batch and emit a benchmark report.

Runs the page-centric parser over a directory of source PDFs, then post-analyzes
the persisted store (ledgers + DOMs) into a benchmark table. Captures peak memory
of the whole parse process (psutil) and streams stderr/stdout for error-class
signals (std::bad_alloc / ONNX / Traceback / FAILED / DEAD) into reports/errors.md.

Usage:
    .venv/Scripts/python.exe scripts/run_parser_benchmark.py \
        --in <sources/pdf> --out <parsed> --batch b01 \
        --reports <reports> [--limit N] [--no-ocr]

Analyze-only (no re-parse; post-analyze an existing store):
    --analyze-only   skip the parser child; just join + report the current store

Writes:
  <reports>/benchmark-<batch>.md  (this run, full per-file detail + parser tail)
  <reports>/benchmark.md          (append-only cumulative one-line per batch)
  <reports>/errors.md             (append-only error-class signals)

The CLI seam is scripts/parse_folder.py; per-doc assembly status / page counts are
read from the page-ledger manifest (plan.json) and dom/ store afterwards.
Dependencies: psutil (optional), PyMuPDF (via seam)."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

PARSE_FOLDER = Path(__file__).resolve().parent / "parse_folder.py"
ERROR_SIGNALS = ("std::bad_alloc", "ONNX", "Traceback", "Error", "FAILED",
                 "DEAD", "dead-letter")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(path: Path, line: str) -> None:
    """Append one line to a report file (creating parents)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


_LastSample = time.monotonic()  # module-level: 1 Hz throttle across calls


def _peak_rss_mb(child: subprocess.Popen) -> float:
    """Best-effort peak RSS (MB) of the parser process tree while it runs.

    Sampled at most once/second. NEVER crashes the run: on any error (including
    WinError 1455 "paging file too small" on heavy loads) it degrades to 0.0 —
    memory telemetry is advisory, the parse is not."""
    global _LastSample
    if psutil is None:
        return 0.0
    now = time.monotonic()
    if now - _LastSample < 1.0:  # throttle: psutil._ppid_map is expensive + fragile
        return 0.0
    _LastSample = now
    try:
        proc = psutil.Process(child.pid)
        peak = proc.memory_info().rss
        for c in proc.children(recursive=True):
            try:
                peak = max(peak, c.memory_info().rss)
            except psutil.Error:
                continue
    except Exception:  # noqa: BLE001 — best-effort telemetry only
        return 0.0
    return peak / (1024 * 1024)


def _ledger_status(parsed: Path, doc_id: str) -> dict:
    """Read one page-ledger plan.json -> compact status dict ({} if no ledger)."""
    lp = parsed / "manifest" / doc_id / "plan.json"
    if not lp.is_file():
        return {"ledger": "missing"}
    try:
        plan = json.loads(lp.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ledger": f"corrupt: {exc}"}
    st: dict[str, int] = {}
    for rec in plan.get("pages", {}).values():
        s = rec.get("status", "?")
        st[s] = st.get(s, 0) + 1
    asm = plan.get("assembly", {})
    return {
        "page_status": st,
        "expected_pages": len(plan.get("expected_page_set", [])),
        "assembled_pages": len(asm.get("assembled_page_set", [])),
        "assembly_status": asm.get("status"),
    }


def _dom_counts(parsed: Path, doc_id: str) -> dict:
    """Count blocks/tables/images/references from the stored DOM."""
    doms = sorted((parsed / "dom" / doc_id).glob("dom-*.docJSON"))
    if not doms:
        return {}
    try:
        doc = json.loads(doms[-1].read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    pages = doc.get("pages", [])
    return {
        "blocks": sum(len(p.get("blocks", [])) for p in pages),
        "tables": sum(len(p.get("tables", [])) for p in pages),
        "images": sum(len(p.get("images", [])) for p in pages),
        "refs": len(doc.get("references", [])),
        "ro_full": len(doc.get("reading_order_full", [])),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="sources", required=True, help="dir of source PDFs")
    ap.add_argument("--out", dest="parsed", required=True, help="parsed store root")
    ap.add_argument("--batch", required=True, help="batch id, e.g. b01")
    ap.add_argument("--reports", required=True, help="reports dir (writes benchmark-<batch>.md)")
    ap.add_argument("--limit", type=int, default=0, help="max files parsed this batch (0=all)")
    ap.add_argument("--no-ocr", action="store_true", help="disable OCR")
    ap.add_argument("--heavy-concurrency", type=int, default=None,
                    help="bound the Docling heavy pool (default: RAM-derived). "
                         "Pass a small value on RAM-limited boxes so the governor "
                         "cannot over-derive workers from TOTAL ram while the box "
                         "is otherwise loaded — prevents paging-file exhaustion.")
    ap.add_argument("--analyze-only", action="store_true",
                    help="skip parser child; just join + report the current store")
    args = ap.parse_args()

    src = Path(args.sources)
    parsed = Path(args.parsed)
    reports = Path(args.reports)
    reports.mkdir(parents=True, exist_ok=True)

    if not src.is_dir():
        print(f"ERROR: sources dir not found: {src}", file=sys.stderr)
        return 2

    pdfs = sorted(src.glob("*.pdf"))[: args.limit or None]
    if not pdfs:
        print(f"ERROR: no PDFs under {src}", file=sys.stderr)
        return 2
    print(f"[bench] batch={args.batch} parsing {len(pdfs)} files from {src}")

    # Phase 1 — run the parser over the folder via the CLI seam (single child proc)
    log_lines: list[str] = []
    peak = 0.0
    wall_s = 0.0
    rc = None
    if not args.analyze_only:
        cmd = [sys.executable, str(PARSE_FOLDER), str(src), str(parsed)]
        if args.no_ocr:
            cmd.append("--no-ocr")
        if args.heavy_concurrency is not None:
            cmd += ["--heavy-concurrency", str(args.heavy_concurrency)]
        t0 = time.monotonic()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        assert proc.stdout is not None
        for line in proc.stdout:
            log_lines.append(line.rstrip())
            if any(sig in line for sig in ERROR_SIGNALS):
                _append(reports / "errors.md", f"[{_now()}] [{args.batch}] {line.strip()}")
            if psutil is not None:
                peak = max(peak, _peak_rss_mb(proc))
        rc = proc.wait()
        wall_s = time.monotonic() - t0

    # Phase 2 — join each source PDF to its ledger by source_hash
    doc_results: list[dict] = []
    for p in pdfs:
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        match = None
        for lp in (parsed / "manifest").glob("*/plan.json"):
            try:
                plan = json.loads(lp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if plan.get("source_hash") == sha:
                match = plan.get("doc_id")
                break
        if match:
            st = {"source_file": p.name, "doc_id": match}
            st.update(_ledger_status(parsed, match))
            st.update(_dom_counts(parsed, match))
            doc_results.append(st)
        else:
            doc_results.append({"source_file": p.name, "assembly_status": "UNPARSED"})

    # Phase 3 — compose benchmark report
    ok = sum(1 for r in doc_results if r.get("assembly_status") == "ok")
    dead = sum(1 for r in doc_results if r.get("assembly_status") == "dead")
    failed = sum(1 for r in doc_results if r.get("assembly_status") == "failed")
    unparsed = sum(1 for r in doc_results if r.get("assembly_status") == "UNPARSED")
    nblocks = sum(r.get("blocks", 0) for r in doc_results)
    ntables = sum(r.get("tables", 0) for r in doc_results)

    report = [
        f"# Benchmark {args.batch} — {_now()}",
        "",
        f"- Files issued: {len(pdfs)} · Assembly ok: `{ok}` · failed: `{failed}` · "
        f"dead: `{dead}` · unparsed: `{unparsed}`",
        f"- Wall time: `{wall_s:.1f}s` · peak RSS: `{peak:.0f} MB` · parser exit: `{rc}`",
        f"- DOM totals: {nblocks} blocks · {ntables} tables",
        (f"- Parse command: `{' '.join(cmd)}`" if not args.analyze_only else
         "- Mode: analyze-only (no parse; existing store)"),
        "",
        "| file | assembly | pages(done/exp) | blocks | tables | refs | ro_full |",
        "|------|----------|------------------|--------|--------|------|---------|",
    ]
    for r in sorted(doc_results, key=lambda x: x.get("source_file", "")):
        pst = r.get("page_status", {})
        done = sum(pst.values())
        report.append(
            f"| {r.get('source_file','?')} | {r.get('assembly_status','?')} "
            f"| {done}/{r.get('expected_pages','?')} "
            f"| {r.get('blocks','-')} | {r.get('tables','-')} "
            f"| {r.get('refs','-')} | {r.get('ro_full','-')} |"
        )
    report.append("")
    report.append("_Parser stdout tail:_")
    report += [f"    {l}" for l in log_lines[-8:]]

    path = reports / f"benchmark-{args.batch}.md"
    path.write_text("\n".join(report), encoding="utf-8")
    _append(reports / "benchmark.md",
            f"- **{args.batch}** ({_now()}): files={len(pdfs)} ok={ok} failed={failed} "
            f"dead={dead} unparsed={unparsed} wall={wall_s:.1f}s peak={peak:.0f}MB "
            f"blocks={nblocks} tables={ntables}")
    print(f"[bench] {args.batch} complete: ok={ok} failed={failed} dead={dead} "
          f"unparsed={unparsed} wall={wall_s:.1f}s peak={peak:.0f}MB")
    print(f"[bench] full report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())