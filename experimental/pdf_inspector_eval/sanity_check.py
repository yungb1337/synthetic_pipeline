"""10-Document Sanity Verification for pdf-inspector hybrid extraction.
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from experimental.pdf_inspector_eval.batch_eval import PDFInspectorBatchEvaluator
from experimental.pdf_inspector_eval.stream_judge import PDFInspectorStreamJudge


def run_sanity():
    print(">>> Running 10-Document Sanity Check on Corpus B...")
    evaluator = PDFInspectorBatchEvaluator(output_dir="artifacts/pdf_inspector_eval/sanity_10")
    targets = evaluator.discover_corpus_targets("corpus_b")

    # Run 10 docs
    eval_results = evaluator.run_batch(targets, limit=10, resume=False)

    # Run LLM Judge on 10 docs
    print("\n>>> Running LLM Judge on the 10 Sanity Documents...")
    judge = PDFInspectorStreamJudge(
        artifact_dir="artifacts/pdf_inspector_eval/sanity_10",
        corpus_type="corpus_b",
        max_workers=4,
    )
    judge.run(resume=False)


if __name__ == "__main__":
    run_sanity()
