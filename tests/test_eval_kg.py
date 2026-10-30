"""Unit Tests for Knowledge Graph & Synthetic Evaluation Suite (eval/kg/)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.kg.models import Assertion, Evidence
from eval.kg.assertion_evaluator import evaluate_assertion_predictions
from eval.kg.metrics.diversity import compute_average_length, compute_vocabulary_entropy
from eval.kg.suite import run_evaluation_suite


def test_gold_sets_and_assertion_evaluator():
    gold_set_path = Path("eval/kg/gold_sets/contraindications_negation.json")
    assert gold_set_path.exists()

    # Simulate perfect prediction
    ev = Evidence(
        assertion_id="pred-1",
        doc_id="guideline-ckd-pain-2024",
        page_index=1,
        block_id="blk-004-012",
        verbatim_text="Duloxetine is not recommended",
    )
    pred_ast = Assertion(
        id="pred-1",
        subject_id="RXNORM:284637",
        predicate="CONTRAINDICATED_IN",
        object_id="MESH:D051436",
        negation=True,
        population="eGFR < 30 mL/min/1.73 m2",
        evidence=[ev],
    )

    preds = {"eval-neg-001": [pred_ast]}
    metrics = evaluate_assertion_predictions(preds, gold_set_path)
    assert metrics.exact_matches >= 1
    assert metrics.precision > 0.0
    assert metrics.negation_accuracy == 1.0
    assert metrics.provenance_anchored == 1


def test_evaluation_suite_runner():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_json = Path(tmpdir) / "eval_report.json"
        results = run_evaluation_suite(gold_dir="eval/kg/gold_sets", output_path=out_json)
        assert "summary" in results
        assert out_json.exists()


def test_diversity_metrics():
    texts = [
        "Metformin is contraindicated in severe renal failure.",
        "Pregabalin requires dose adjustment in elderly patients.",
        "Duloxetine should be avoided when eGFR is low.",
    ]
    entropy = compute_vocabulary_entropy(texts)
    assert entropy > 0.0
    avg_len = compute_average_length(texts)
    assert avg_len > 0.0
