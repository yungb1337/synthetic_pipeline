#!/usr/bin/env python
"""Multi-Box Cluster Sharding Runner (Architecture C / B3).

Demonstrates and executes distributed partition runs across multiple cluster nodes
or processes with a shared or mounted store root.

Usage:
    # Run shard 0 of a 4-node cluster
    python scripts/run_cluster_shards.py --in /shared/corpus --out /shared/store --shard-index 0 --shard-total 4

    # Simulate full 4-node cluster execution locally in parallel
    python scripts/run_cluster_shards.py --in /shared/corpus --out /shared/store --simulate-nodes 4
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _resolve_venv_python() -> str:
    here = Path(__file__).resolve().parent
    repo = here.parent
    for c in [sys.executable, repo / ".venv" / "Scripts" / "python.exe", repo / ".venv" / "bin" / "python"]:
        cp = Path(c)
        if cp.is_file():
            return str(cp)
    return "python"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Multi-Box Cluster Sharding (Architecture C / B3)")
    ap.add_argument("--in", dest="input", required=True, help="corpus directory (shared NFS/mount)")
    ap.add_argument("--out", dest="output", default="parser_out", help="shared output store directory")
    ap.add_argument("--shard-index", type=int, default=0, help="node shard index (0 to shard-total - 1)")
    ap.add_argument("--shard-total", type=int, default=1, help="total node count / shards")
    ap.add_argument("--simulate-nodes", type=int, default=0, help="simulate N nodes concurrently")
    ap.add_argument("--no-ocr", action="store_true", help="disable OCR")
    args = ap.parse_args(argv)

    py = _resolve_venv_python()
    base_cmd = [py, "-m", "app.processing.cli", "--in", args.input, "--out", args.output]
    if args.no_ocr:
        base_cmd.append("--no-ocr")

    if args.simulate_nodes > 1:
        total = args.simulate_nodes
        print(f"[cluster_shards] Simulating {total} cluster node workers concurrently...")
        procs = []
        for idx in range(total):
            manifest = f"work/cluster_node_{idx}_manifest.json"
            cmd = list(base_cmd) + [
                "--shard-index", str(idx),
                "--shard-total", str(total),
                "--manifest", manifest,
            ]
            procs.append(subprocess.Popen(cmd))

        exit_codes = [p.wait() for p in procs]
        if any(c != 0 for c in exit_codes):
            print(f"[cluster_shards] Error: {sum(1 for c in exit_codes if c != 0)} nodes failed", file=sys.stderr)
            return 1
        print(f"[cluster_shards] All {total} cluster nodes finished successfully.")
        return 0

    # Single node execution
    cmd = list(base_cmd) + [
        "--shard-index", str(args.shard_index),
        "--shard-total", str(args.shard_total),
    ]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
