"""Qualified Assertion Evaluation Engine for MedFactory AI.

Evaluates extracted Qualified Assertions against gold benchmark datasets.
Computes multi-dimensional extraction fidelity:
- Subject & Object Canonical ID linking precision / recall
- Predicate F1
- Qualifier Accuracy:
    - Negation match (critical for contraindication safety)
    - Population qualifier alignment
    - Dosage constraint preservation
    - Physical DOM block provenance anchoring
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.kg.models import Assertion


@dataclass
class EvalMetrics:
    total_gold: int = 0
    total_pred: int = 0
    exact_matches: int = 0
    predicate_correct: int = 0
    negation_correct: int = 0
    population_correct: int = 0
    dosage_correct: int = 0
    provenance_anchored: int = 0

    @property
    def precision(self) -> float:
        return self.exact_matches / self.total_pred if self.total_pred > 0 else 0.0

    @property
    def recall(self) -> float:
        return self.exact_matches / self.total_gold if self.total_gold > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * (p * r) / (p + r) if (p + r) > 0 else 0.0

    @property
    def negation_accuracy(self) -> float:
        return self.negation_correct / self.exact_matches if self.exact_matches > 0 else 0.0

    @property
    def dosage_accuracy(self) -> float:
        return self.dosage_correct / self.exact_matches if self.exact_matches > 0 else 0.0


def evaluate_assertion_predictions(
    predictions_by_test_id: dict[str, list[Assertion]],
    gold_set_path: Path,
) -> EvalMetrics:
    with open(gold_set_path, "r", encoding="utf-8") as f:
        gold_cases = json.load(f)

    metrics = EvalMetrics()

    for case in gold_cases:
        test_id = case["test_id"]
        gold_assertions = case["gold_assertions"]
        metrics.total_gold += len(gold_assertions)

        preds = predictions_by_test_id.get(test_id, [])
        metrics.total_pred += len(preds)

        for gold in gold_assertions:
            # Find candidate prediction matching (subject_id, object_id)
            matched_pred = None
            for pred in preds:
                if (
                    pred.subject_id == gold["subject_id"]
                    and pred.object_id == gold["object_id"]
                ):
                    matched_pred = pred
                    break

            if matched_pred:
                metrics.exact_matches += 1
                if matched_pred.predicate == gold["predicate"]:
                    metrics.predicate_correct += 1
                if matched_pred.negation == gold.get("negation", False):
                    metrics.negation_correct += 1
                if (
                    gold.get("population", "").strip().lower()
                    in matched_pred.population.strip().lower()
                    or not gold.get("population")
                ):
                    metrics.population_correct += 1
                if (
                    gold.get("dosage", "").strip().lower()
                    in matched_pred.dosage.strip().lower()
                    or not gold.get("dosage")
                ):
                    metrics.dosage_correct += 1
                if matched_pred.evidence and len(matched_pred.evidence) > 0:
                    metrics.provenance_anchored += 1

    return metrics


def format_eval_report(name: str, metrics: EvalMetrics) -> str:
    return (
        f"====================================================\n"
        f" EVALUATION REPORT: {name}\n"
        f"====================================================\n"
        f" Gold Assertions:       {metrics.total_gold}\n"
        f" Predicted Assertions:  {metrics.total_pred}\n"
        f" Entity-Pair Matches:   {metrics.exact_matches}\n"
        f" Precision:             {metrics.precision:.2%}\n"
        f" Recall:                {metrics.recall:.2%}\n"
        f" F1 Score:              {metrics.f1:.2%}\n"
        f"----------------------------------------------------\n"
        f" Qualifier Fidelity:\n"
        f"  - Negation Accuracy:  {metrics.negation_accuracy:.2%}\n"
        f"  - Dosage Accuracy:    {metrics.dosage_accuracy:.2%}\n"
        f"  - Provenance Linked:  {metrics.provenance_anchored}/{metrics.exact_matches}\n"
        f"====================================================\n"
    )
