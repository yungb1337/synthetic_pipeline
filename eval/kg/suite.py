"""Comprehensive Evaluation Suite for Knowledge Graph & Synthetic Generation.

Runs extraction fidelity benchmarks against gold sets and reports
Trust Engine pass rates, qualifier accuracy, and lexical diversity.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from eval.kg.assertion_evaluator import evaluate_assertion_predictions, format_eval_report
from eval.kg.metrics.diversity import compute_average_length, compute_vocabulary_entropy


def run_evaluation_suite(
    gold_dir: Path | str = "eval/kg/gold_sets",
    predictions_map: dict[str, Any] | None = None,
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    """Runs evaluation suite across all gold benchmark sets."""
    gold_p = Path(gold_dir)
    results: dict[str, Any] = {}
    preds = predictions_map or {}

    total_gold = 0
    total_matches = 0
    total_preds = 0

    print("====================================================")
    print(" KNOWLEDGE GRAPH & SYNTHESIS EVALUATION SUITE")
    print("====================================================")

    for gold_file in gold_p.glob("*.json"):
        set_name = gold_file.stem
        metrics = evaluate_assertion_predictions(preds, gold_file)
        results[set_name] = {
            "gold_count": metrics.total_gold,
            "pred_count": metrics.total_pred,
            "exact_matches": metrics.exact_matches,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "f1": metrics.f1,
            "negation_accuracy": metrics.negation_accuracy,
            "dosage_accuracy": metrics.dosage_accuracy,
            "provenance_anchored": metrics.provenance_anchored,
        }
        total_gold += metrics.total_gold
        total_matches += metrics.exact_matches
        total_preds += metrics.total_pred

        print(format_eval_report(set_name, metrics))

    overall_precision = total_matches / total_preds if total_preds > 0 else 0.0
    overall_recall = total_matches / total_gold if total_gold > 0 else 0.0
    overall_f1 = (
        2 * (overall_precision * overall_recall) / (overall_precision + overall_recall)
        if (overall_precision + overall_recall) > 0
        else 0.0
    )

    results["summary"] = {
        "total_gold": total_gold,
        "total_pred": total_preds,
        "total_matches": total_matches,
        "overall_precision": overall_precision,
        "overall_recall": overall_recall,
        "overall_f1": overall_f1,
    }

    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"Evaluation report written to: {out_p}")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KG & Synthetic Data Evaluation Suite")
    parser.add_argument(
        "--gold-dir",
        type=str,
        default="eval/kg/gold_sets",
        help="Path to directory containing gold benchmark JSON sets",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="eval_report.json",
        help="Path to output JSON summary",
    )
    args = parser.parse_args()

    run_evaluation_suite(gold_dir=args.gold_dir, output_path=args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
