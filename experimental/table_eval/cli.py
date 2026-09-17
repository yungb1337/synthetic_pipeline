"""CLI entrypoint for the Table + Layout Model Benchmark.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import BenchmarkConfig, PERMUTATIONS
from .runner import BenchmarkRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Table + Layout Model Benchmark CLI")

    parser.add_argument(
        "--list-strategies",
        action="store_true",
        help="List all defined candidate permutations (P000-P016)",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        help="Run a specific strategy ID (e.g. P002, P008, P014)",
    )
    parser.add_argument(
        "--strategies",
        type=str,
        help="Comma-separated strategy IDs (e.g. P002,P003,P008)",
    )
    parser.add_argument(
        "--stage",
        type=str,
        choices=["smoke", "performance", "judge", "all"],
        default="smoke",
        help="Benchmark stage to run",
    )
    parser.add_argument(
        "--corpus",
        type=str,
        choices=["smoke", "reference", "expanded"],
        default="smoke",
        help="Corpus subset to evaluate against",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="Rendering DPI for rasterization (default: 150)",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.list_strategies:
        print("\n=== Table & Layout Benchmark Strategy Registry ===")
        print(f"{'ID':<6} | {'Strategy Name':<28} | {'VRAM / GPU':<10} | {'Description'}")
        print("-" * 90)
        for s_id, s in sorted(PERMUTATIONS.items()):
            gpu_str = "GPU Req" if s.requires_gpu else "CPU Safe"
            print(f"{s.id:<6} | {s.name:<28} | {gpu_str:<10} | {s.description}")
        return

    config = BenchmarkConfig(render_dpi=args.dpi)
    runner = BenchmarkRunner(config)

    # Determine Corpus paths
    ref_dir = config.reference_corpus_dir
    all_pdfs = sorted(ref_dir.glob("*.pdf")) if ref_dir.exists() else []

    if args.corpus == "smoke":
        # 2 representative PDFs: 2302.04143 (3-page multi-table paper) and PMC10234567 (single-page)
        pdf_paths = [p for p in all_pdfs if p.name in ("2302.04143.pdf", "PMC10234567.pdf")]
        if not pdf_paths and all_pdfs:
            pdf_paths = all_pdfs[:2]
    elif args.corpus == "reference":
        pdf_paths = all_pdfs
    else:
        pdf_paths = all_pdfs

    if not pdf_paths:
        print(f"[ERROR] No PDF files found in {ref_dir}")
        sys.exit(1)

    # Determine Strategies
    if args.strategy:
        strat_ids = [args.strategy]
    elif args.strategies:
        strat_ids = [s.strip() for s in args.strategies.split(",") if s.strip()]
    else:
        # Default run set for stage
        if args.stage == "smoke":
            strat_ids = ["P001", "P002", "P003", "P004", "P005", "P008", "P010", "P014"]
        else:
            strat_ids = ["P002", "P003", "P008", "P010", "P014"]

    print(f"Starting Benchmark: Stage='{args.stage}', Corpus='{args.corpus}' ({len(pdf_paths)} docs), Strategies={strat_ids}")
    runner.run_stage(stage=args.stage, strategy_ids=strat_ids, pdf_paths=pdf_paths)


if __name__ == "__main__":
    main()
