#!/usr/bin/env python
"""run_all_waves.py — self-healing orchestrator for parsing waves b03..b07.

Executes all 5 waves across the 945-document curated corpus:
  - Wave 1 (b03): offset 0, limit 200 -> parsed-b03
  - Wave 2 (b04): offset 200, limit 200 -> parsed-b04
  - Wave 3 (b05): offset 400, limit 200 -> parsed-b05
  - Wave 4 (b06): offset 600, limit 200 -> parsed-b06
  - Wave 5 (b07): offset 800, limit 145 -> parsed-b07

Resilience pattern:
  If a child parser process exits mid-batch (e.g. native C++ allocator reset),
  the runner detects remaining unparsed files and restarts immediately with
  `resume=True`. Already-completed documents are verified in milliseconds from
  their on-disk ledgers (`plan.json`) and skipped.
  Once all documents in a wave are parsed, it runs the benchmark post-analysis
  to produce `reports/benchmark-<batch>.md` and advances to the next wave.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure CPU execution for PyTorch/ONNX to prevent CUDA handle errors on Windows
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["TORCHDYNAMO_DISABLE"] = "1"

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_PDF = REPO_ROOT / "checkpoints" / "run" / "run-2026-09-04-parser-reliability" / "sources" / "pdf"
REPORTS_DIR = REPO_ROOT / "checkpoints" / "run" / "run-2026-09-04-parser-reliability" / "reports"
RUN_DIR = REPO_ROOT / "checkpoints" / "run" / "run-2026-09-04-parser-reliability"

WAVES = [
    ("b03", 0, 200),
    ("b04", 200, 200),
    ("b05", 400, 200),
    ("b06", 600, 200),
    ("b07", 800, 145),
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_python() -> str:
    venv_py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable


def _get_batch_status(parsed_dir: Path, batch_pdfs: list[Path]) -> tuple[int, int, int]:
    """Returns (ok_count, failed_or_pending_count, unparsed_count)."""
    manifest_dir = parsed_dir / "manifest"
    if not manifest_dir.exists():
        return 0, 0, len(batch_pdfs)

    known_hashes: dict[str, str] = {}
    for plan_file in manifest_dir.glob("*/plan.json"):
        try:
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
            sh = plan.get("source_hash")
            st = plan.get("assembly", {}).get("status", "pending")
            if sh:
                known_hashes[sh] = st
        except Exception:
            continue

    ok = 0
    other = 0
    unparsed = 0
    for p in batch_pdfs:
        try:
            data = p.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if sha in known_hashes:
                st = known_hashes[sha]
                if st == "ok":
                    ok += 1
                else:
                    other += 1
            else:
                unparsed += 1
        except Exception:
            unparsed += 1

    return ok, other, unparsed


def main() -> int:
    py = _resolve_python()
    bench_script = REPO_ROOT / "scripts" / "run_parser_benchmark.py"
    parse_folder_script = REPO_ROOT / "scripts" / "parse_folder.py"
    progress_log = REPORTS_DIR / "corpus-batch-progress.log"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    all_pdfs = sorted(SOURCES_PDF.glob("*.pdf"))
    total_files = len(all_pdfs)

    print(f"[{_now()}] [pipeline] Starting 5-wave corpus parse run ({total_files} PDFs total)")
    with progress_log.open("a", encoding="utf-8") as pf:
        pf.write(f"\n[{_now()}] === Starting resilient 5-wave corpus run ({total_files} total) ===\n")

    t_start = time.monotonic()

    for batch_id, offset, limit in WAVES:
        batch_pdfs = all_pdfs[offset : offset + limit]
        parsed_out = RUN_DIR / f"parsed-{batch_id}"
        parsed_out.mkdir(parents=True, exist_ok=True)

        print(f"\n{'='*70}\n[{_now()}] [pipeline] Wave {batch_id}: offset={offset}, limit={limit} ({len(batch_pdfs)} files) -> {parsed_out.name}\n{'='*70}", flush=True)

        wave_t0 = time.monotonic()
        pass_num = 0
        consecutive_zero_progress = 0

        while True:
            pass_num += 1
            ok, other, unparsed = _get_batch_status(parsed_out, batch_pdfs)
            status_line = f"[{_now()}] [{batch_id}] Pass {pass_num}: ok={ok}, in-flight/pending={other}, unparsed={unparsed} (total={len(batch_pdfs)})"
            print(status_line, flush=True)
            with progress_log.open("a", encoding="utf-8") as pf:
                pf.write(f"{status_line}\n")

            if unparsed == 0 and other == 0:
                print(f"[{_now()}] [{batch_id}] All {len(batch_pdfs)} documents parsed successfully!", flush=True)
                break

            prev_unparsed = unparsed
            cmd = [
                py, str(parse_folder_script),
                "--in", str(SOURCES_PDF),
                "--out", str(parsed_out),
                "--offset", str(offset),
                "--limit", str(limit),
                "--heavy-concurrency", "1",
            ]

            t_run0 = time.monotonic()
            rc = subprocess.call(cmd)
            run_duration = time.monotonic() - t_run0

            new_ok, new_other, new_unparsed = _get_batch_status(parsed_out, batch_pdfs)
            progress_made = (new_ok - ok) + max(0, prev_unparsed - new_unparsed)

            msg = f"[{_now()}] [{batch_id}] Child process exited (rc={rc}) after {run_duration:.1f}s | Progress: +{progress_made} newly finished"
            print(msg, flush=True)
            with progress_log.open("a", encoding="utf-8") as pf:
                pf.write(f"{msg}\n")

            if progress_made <= 0:
                consecutive_zero_progress += 1
                if consecutive_zero_progress >= 8:
                    print(f"[{_now()}] [{batch_id}] WARNING: 8 consecutive passes with zero progress. Advancing to benchmark.", flush=True)
                    break
                time.sleep(2)
            else:
                consecutive_zero_progress = 0

        # Run benchmark post-analysis for this batch
        print(f"[{_now()}] [{batch_id}] Running benchmark post-analysis...", flush=True)
        bench_cmd = [
            py, str(bench_script),
            "--in", str(SOURCES_PDF),
            "--out", str(parsed_out),
            "--batch", batch_id,
            "--reports", str(REPORTS_DIR),
            "--offset", str(offset),
            "--limit", str(limit),
            "--analyze-only",
        ]
        subprocess.call(bench_cmd)
        wave_elapsed = time.monotonic() - wave_t0
        done_msg = f"[{_now()}] [pipeline] Wave {batch_id} fully analyzed in {wave_elapsed/60:.1f} mins"
        print(done_msg, flush=True)
        with progress_log.open("a", encoding="utf-8") as pf:
            pf.write(f"{done_msg}\n")

    total_elapsed = time.monotonic() - t_start
    final_msg = f"[{_now()}] [pipeline] All 5 waves completed in {total_elapsed/3600:.2f} hours"
    print(f"\n{'='*70}\n{final_msg}\n{'='*70}", flush=True)
    with progress_log.open("a", encoding="utf-8") as pf:
        pf.write(f"{final_msg}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
