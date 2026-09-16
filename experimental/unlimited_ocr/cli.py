"""Turnkey CLI for running Unlimited-OCR evaluation.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from .config import UnlimitedOCRConfig
from .runner import UnlimitedOCREvaluationRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated Unlimited-OCR evaluation and comparison.")
    parser.add_argument("--corpus", default="reference", help="Corpus to evaluate: 'reference', 'reliability', 'corpus_1000', or path to PDF folder/file")
    parser.add_argument("--gpu", action="store_true", help="Enable GPU acceleration via PyTorch CUDA")
    parser.add_argument("--limit", type=int, default=6, help="Maximum number of documents to evaluate (0 for all)")
    parser.add_argument("--repeat", type=int, default=2, help="Number of repeat runs for determinism check (Experiment C)")
    parser.add_argument("--no-judge", action="store_true", help="Skip Gemini LLM Judge evaluation")
    parser.add_argument("--judge-model", default="gemini-3.5-flash-lite", help="LLM Judge model name")
    parser.add_argument("--artifacts-dir", default=None, help="Custom artifacts directory")
    parser.add_argument("--evaluation-dir", default=None, help="Custom evaluation output directory")
    parser.add_argument("--report-path", default=None, help="Custom markdown report output path")
    args = parser.parse_args()

    config = UnlimitedOCRConfig(judge_model=args.judge_model, use_gpu=args.gpu)
    if args.artifacts_dir:
        config.artifacts_dir = Path(args.artifacts_dir)
    if args.evaluation_dir:
        config.evaluation_dir = Path(args.evaluation_dir)
    if args.report_path:
        config.report_path = Path(args.report_path)

    runner = UnlimitedOCREvaluationRunner(config)
    try:
        report = runner.run_evaluation(
            corpus_name=args.corpus,
            limit=args.limit,
            repeat_count=args.repeat,
            run_judge=not args.no_judge,
        )
        print(f"\n[SUCCESS] Unlimited-OCR evaluation finished successfully. Success rate: {report.get('success_rate')*100:.1f}%")
        return 0
    except Exception as exc:
        print(f"\n[ERROR] Unlimited-OCR evaluation failed: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
