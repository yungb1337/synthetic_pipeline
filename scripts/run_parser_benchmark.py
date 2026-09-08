#!/usr/bin/env python
"""run_parser_benchmark.py — parse a corpus batch and emit a benchmark report.

Runs the page-centric parser over a directory of source PDFs, then post-analyzes
the persisted store (ledgers + DOMs) into a benchmark table. Captures fine-grained
telemetry including:
  - time per doc & time per page
  - peak memory per doc & peak memory per corpus (single-worker and total tree RSS)
  - system memory usage & swap/pagefile delta
  - streams stderr/stdout for error-class signals into reports/errors.md

Usage:
    .venv/Scripts/python.exe scripts/run_parser_benchmark.py \
        --in <sources/pdf> --out <parsed> --batch b01 \
        --reports <reports> [--limit N] [--offset N] [--no-ocr] [--heavy-concurrency N]

Analyze-only (no re-parse; post-analyze an existing store):
    --analyze-only   skip the parser child; just join + report the current store

Writes:
  <reports>/benchmark-<batch>.md  (this run, full per-file detail + telemetry)
  <reports>/benchmark.md          (append-only cumulative one-line per batch)
  <reports>/errors.md             (append-only error-class signals)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import psutil
except ImportError:
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


class ProcessTreeMemorySampler:
    """Background sampler recording process tree RSS at high frequency."""

    def __init__(self, pid: int, interval: float = 0.15):
        self.pid = pid
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.peak_worker_rss_mb = 0.0
        self.peak_total_tree_rss_mb = 0.0
        self.current_window_max_worker_mb = 0.0
        self.current_window_max_total_mb = 0.0

    def start(self) -> None:
        if psutil is None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def pop_window_peaks(self) -> tuple[float, float]:
        """Return (max_worker_mb, max_total_tree_mb) since last pop and reset window."""
        with self.lock:
            w_peak = self.current_window_max_worker_mb
            t_peak = self.current_window_max_total_mb
            self.current_window_max_worker_mb = 0.0
            self.current_window_max_total_mb = 0.0
            return w_peak, t_peak

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                parent = psutil.Process(self.pid)
                procs = [parent] + parent.children(recursive=True)
                rss_list = []
                for p in procs:
                    try:
                        rss_list.append(p.memory_info().rss / (1024 * 1024))
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                if rss_list:
                    max_worker = max(rss_list)
                    total_tree = sum(rss_list)
                    with self.lock:
                        if max_worker > self.peak_worker_rss_mb:
                            self.peak_worker_rss_mb = max_worker
                        if total_tree > self.peak_total_tree_rss_mb:
                            self.peak_total_tree_rss_mb = total_tree
                        if max_worker > self.current_window_max_worker_mb:
                            self.current_window_max_worker_mb = max_worker
                        if total_tree > self.current_window_max_total_mb:
                            self.current_window_max_total_mb = total_tree
            except Exception:
                pass
            time.sleep(self.interval)


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
    ap.add_argument("--offset", type=int, default=0, help="starting file index (0=first)")
    ap.add_argument("--no-ocr", action="store_true", help="disable OCR")
    ap.add_argument("--heavy-concurrency", type=int, default=None,
                    help="bound the Docling heavy pool (default: RAM-derived).")
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

    pdfs = sorted(src.glob("*.pdf"))
    if args.offset:
        pdfs = pdfs[args.offset:]
    if args.limit:
        pdfs = pdfs[:args.limit]
    if not pdfs:
        print(f"ERROR: no PDFs under {src}", file=sys.stderr)
        return 2
    print(f"[bench] batch={args.batch} parsing {len(pdfs)} files from {src}")

    # Initial memory snapshot
    sys_mem_start = psutil.virtual_memory() if psutil else None
    sys_swap_start = psutil.swap_memory() if psutil else None

    # Phase 1 — run the parser over the folder via the CLI seam
    log_lines: list[str] = []
    wall_s = 0.0
    rc = None
    doc_telemetry: dict[str, dict] = {}  # filename -> {time_ms, pages, peak_worker_mb, peak_tree_mb}
    peak_worker_mb = 0.0
    peak_corpus_tree_mb = 0.0

    if not args.analyze_only:
        t0 = time.monotonic()
        pass_num = 0
        consecutive_stalls = 0

        while True:
            # Check how many target batch pdfs are already ok
            ok_cnt = 0
            for lp in (parsed / "manifest").glob("*/plan.json"):
                try:
                    p_data = json.loads(lp.read_text(encoding="utf-8"))
                    if p_data.get("assembly", {}).get("status") == "ok":
                        ok_cnt += 1
                except Exception:
                    pass

            if ok_cnt >= len(pdfs):
                break

            pass_num += 1
            cmd = [sys.executable, str(PARSE_FOLDER), str(src), str(parsed)]
            if args.no_ocr:
                cmd.append("--no-ocr")
            if args.heavy_concurrency is not None:
                cmd += ["--heavy-concurrency", str(args.heavy_concurrency)]
            if args.limit:
                cmd += ["--limit", str(args.limit)]
            if args.offset:
                cmd += ["--offset", str(args.offset)]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert proc.stdout is not None

            sampler = ProcessTreeMemorySampler(proc.pid, interval=0.15)
            sampler.start()

            current_doc_name: str | None = None
            current_doc_pages: int = 0

            for line in proc.stdout:
                raw_line = line.rstrip()
                print(raw_line, flush=True)
                log_lines.append(raw_line)

                if any(sig in raw_line for sig in ERROR_SIGNALS):
                    _append(reports / "errors.md", f"[{_now()}] [{args.batch}] {raw_line.strip()}")

                # Telemetry parsing:
                # Matches "OK   PMC12345.pdf  pdf  pages=12  ..."
                ok_match = re.match(r"^OK\s+(\S+)\s+\S+\s+pages=(\d+)", raw_line)
                skip_match = re.match(r"^SKIP\s+(\S+)", raw_line)
                fail_match = re.match(r"^FAIL\s+(\S+)", raw_line)

                if ok_match:
                    current_doc_name = ok_match.group(1).strip()
                    current_doc_pages = int(ok_match.group(2))
                elif skip_match:
                    fname = skip_match.group(1).strip()
                    w_peak, t_peak = sampler.pop_window_peaks()
                    if fname not in doc_telemetry:
                        doc_telemetry[fname] = {
                            "pages": 0,
                            "time_ms": 0.0,
                            "peak_worker_mb": w_peak,
                            "peak_tree_mb": t_peak,
                        }
                elif fail_match:
                    fname = fail_match.group(1).strip()
                    w_peak, t_peak = sampler.pop_window_peaks()
                    if fname not in doc_telemetry:
                        doc_telemetry[fname] = {
                            "pages": 0,
                            "time_ms": 0.0,
                            "peak_worker_mb": w_peak,
                            "peak_tree_mb": t_peak,
                        }

                # Matches "      timings: ... total=1234.5ms"
                timing_match = re.search(r"total=([\d\.]+)ms", raw_line)
                if timing_match and current_doc_name:
                    t_ms = float(timing_match.group(1))
                    w_peak, t_peak = sampler.pop_window_peaks()
                    doc_telemetry[current_doc_name] = {
                        "pages": current_doc_pages,
                        "time_ms": t_ms,
                        "peak_worker_mb": w_peak,
                        "peak_tree_mb": t_peak,
                    }
                    current_doc_name = None

            rc = proc.wait()
            sampler.stop()

            peak_worker_mb = max(peak_worker_mb, sampler.peak_worker_rss_mb)
            peak_corpus_tree_mb = max(peak_corpus_tree_mb, sampler.peak_total_tree_rss_mb)

            # Check progress made in this pass
            new_ok_cnt = 0
            for lp in (parsed / "manifest").glob("*/plan.json"):
                try:
                    p_data = json.loads(lp.read_text(encoding="utf-8"))
                    if p_data.get("assembly", {}).get("status") == "ok":
                        new_ok_cnt += 1
                except Exception:
                    pass

            if new_ok_cnt == ok_cnt:
                consecutive_stalls += 1
                if consecutive_stalls >= 3:
                    print(f"[{_now()}] [{args.batch}] Stopping after 3 consecutive passes with 0 progress", flush=True)
                    break
            else:
                consecutive_stalls = 0

        wall_s = time.monotonic() - t0

    sys_mem_end = psutil.virtual_memory() if psutil else None
    sys_swap_end = psutil.swap_memory() if psutil else None

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

        st = {"source_file": p.name}
        telem = doc_telemetry.get(p.name, {})
        time_ms = telem.get("time_ms", 0.0)
        t_pages = telem.get("pages", 0)
        ms_per_page = (time_ms / t_pages) if t_pages > 0 else 0.0

        st["time_ms"] = time_ms
        st["ms_per_page"] = ms_per_page
        st["peak_worker_mb"] = telem.get("peak_worker_mb", 0.0)
        st["peak_tree_mb"] = telem.get("peak_tree_mb", 0.0)

        if match:
            st["doc_id"] = match
            st.update(_ledger_status(parsed, match))
            st.update(_dom_counts(parsed, match))
            # Fallback expected_pages if telemetry didn't capture
            if t_pages == 0:
                exp = st.get("expected_pages") or 0
                if exp > 0 and time_ms > 0:
                    st["ms_per_page"] = time_ms / exp
            doc_results.append(st)
        else:
            st["assembly_status"] = "UNPARSED"
            doc_results.append(st)

    # Phase 3 — compute aggregate statistics
    ok = sum(1 for r in doc_results if r.get("assembly_status") == "ok")
    dead = sum(1 for r in doc_results if r.get("assembly_status") == "dead")
    failed = sum(1 for r in doc_results if r.get("assembly_status") == "failed")
    unparsed = sum(1 for r in doc_results if r.get("assembly_status") == "UNPARSED")
    nblocks = sum(r.get("blocks", 0) for r in doc_results)
    ntables = sum(r.get("tables", 0) for r in doc_results)
    nrefs = sum(r.get("refs", 0) for r in doc_results)

    total_pages_done = sum(
        sum(r.get("page_status", {}).values()) for r in doc_results
    )
    ok_times = [r["time_ms"] for r in doc_results if r.get("time_ms", 0) > 0]
    avg_ms_per_doc = (sum(ok_times) / len(ok_times)) if ok_times else 0.0
    avg_ms_per_page = (sum(ok_times) / total_pages_done) if total_pages_done > 0 and ok_times else 0.0

    # System memory reporting
    mem_avail_start_gb = (sys_mem_start.available / (1024**3)) if sys_mem_start else 0.0
    mem_avail_end_gb = (sys_mem_end.available / (1024**3)) if sys_mem_end else 0.0
    swap_used_start_gb = (sys_swap_start.used / (1024**3)) if sys_swap_start else 0.0
    swap_used_end_gb = (sys_swap_end.used / (1024**3)) if sys_swap_end else 0.0

    # Phase 4 — compose comprehensive benchmark report
    report = [
        f"# Benchmark {args.batch} — {_now()}",
        "",
        "## Summary",
        f"- **Documents**: {len(pdfs)} issued · `{ok}` OK · `{failed}` failed · `{dead}` dead · `{unparsed}` unparsed",
        f"- **Throughput & Timing**:",
        f"  - Total wall time: `{wall_s:.1f}s` ({wall_s/60:.2f} mins)",
        f"  - Total pages parsed: `{total_pages_done}`",
        f"  - Mean time per doc: `{avg_ms_per_doc:.1f} ms` ({avg_ms_per_doc/1000:.2f}s)",
        f"  - Mean time per page: `{avg_ms_per_page:.1f} ms` ({avg_ms_per_page/1000:.2f}s)",
        f"- **Memory Telemetry**:",
        f"  - Peak memory per corpus (Total tree RSS): `{peak_corpus_tree_mb:.0f} MB`",
        f"  - Peak worker memory (Max single-process RSS): `{peak_worker_mb:.0f} MB`",
        f"  - Host RAM available: `{mem_avail_start_gb:.2f} GB` start -> `{mem_avail_end_gb:.2f} GB` end",
        f"  - Pagefile/Swap committed: `{swap_used_start_gb:.2f} GB` start -> `{swap_used_end_gb:.2f} GB` end",
        f"- **Extraction Yield**: `{nblocks}` blocks · `{ntables}` tables · `{nrefs}` references",
        (f"- **Command**: `{' '.join(cmd)}`" if not args.analyze_only else
         "- **Mode**: analyze-only (no parse; existing store)"),
        "",
        "## Document Metrics",
        "| file | assembly | pages(done/exp) | time(s) | ms/page | peak_worker(MB) | peak_tree(MB) | blocks | tables | refs | ro_full |",
        "|------|----------|------------------|---------|---------|-----------------|---------------|--------|--------|------|---------|",
    ]

    for r in sorted(doc_results, key=lambda x: x.get("source_file", "")):
        pst = r.get("page_status", {})
        done = sum(pst.values())
        t_s = f"{r.get('time_ms', 0) / 1000:.2f}" if r.get('time_ms', 0) > 0 else "-"
        ms_p = f"{r.get('ms_per_page', 0):.0f}" if r.get('ms_per_page', 0) > 0 else "-"
        p_w = f"{r.get('peak_worker_mb', 0):.0f}" if r.get('peak_worker_mb', 0) > 0 else "-"
        p_t = f"{r.get('peak_tree_mb', 0):.0f}" if r.get('peak_tree_mb', 0) > 0 else "-"

        report.append(
            f"| {r.get('source_file','?')} | {r.get('assembly_status','?')} "
            f"| {done}/{r.get('expected_pages','?')} "
            f"| {t_s} | {ms_p} | {p_w} | {p_t} "
            f"| {r.get('blocks','-')} | {r.get('tables','-')} "
            f"| {r.get('refs','-')} | {r.get('ro_full','-')} |"
        )

    report.append("")
    report.append("## Parser Log Tail")
    report += [f"    {l}" for l in log_lines[-8:]]

    path = reports / f"benchmark-{args.batch}.md"
    path.write_text("\n".join(report), encoding="utf-8")

    # One-line summary in benchmark.md
    _append(
        reports / "benchmark.md",
        f"- **{args.batch}** ({_now()}): files={len(pdfs)} ok={ok} failed={failed} "
        f"dead={dead} unparsed={unparsed} wall={wall_s:.1f}s avg_doc={avg_ms_per_doc/1000:.2f}s "
        f"avg_page={avg_ms_per_page:.0f}ms peak_tree={peak_corpus_tree_mb:.0f}MB "
        f"peak_worker={peak_worker_mb:.0f}MB blocks={nblocks} tables={ntables}",
    )

    print(f"\n[bench] {args.batch} complete: ok={ok} failed={failed} dead={dead} "
          f"unparsed={unparsed} wall={wall_s:.1f}s avg_doc={avg_ms_per_doc/1000:.2f}s "
          f"peak_tree={peak_corpus_tree_mb:.0f}MB peak_worker={peak_worker_mb:.0f}MB")
    print(f"[bench] full report written to: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
