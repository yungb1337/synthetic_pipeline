"""Artifact storage, experiment registry, and JSON export utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.parser.dom.models import Document


class BenchmarkArtifactManager:
    """Manages isolated experiment directories under artifacts/table_eval/ and evaluation/table_benchmark/."""

    def __init__(self, artifacts_base: Path, eval_base: Path):
        self.artifacts_base = Path(artifacts_base)
        self.eval_base = Path(eval_base)

        self.artifacts_base.mkdir(parents=True, exist_ok=True)
        self.eval_base.mkdir(parents=True, exist_ok=True)

    def get_strategy_dir(self, strategy_id: str) -> Path:
        p = self.artifacts_base / strategy_id
        p.mkdir(parents=True, exist_ok=True)
        (p / "raw_output").mkdir(exist_ok=True)
        (p / "normalized_output").mkdir(exist_ok=True)
        (p / "logs").mkdir(exist_ok=True)
        return p

    def save_document_dom(
        self, strategy_id: str, document_id: str, dom: Document
    ) -> Path:
        s_dir = self.get_strategy_dir(strategy_id)
        out_path = s_dir / "normalized_output" / f"{document_id}.parsed.v1.docJSON"
        out_path.write_text(dom.model_dump_json(indent=2), encoding="utf-8")
        return out_path

    def save_runtime_telemetry(
        self, strategy_id: str, document_id: str, data: dict[str, Any]
    ) -> None:
        s_dir = self.get_strategy_dir(strategy_id)
        out_path = s_dir / "raw_output" / f"{document_id}.runtime.json"
        out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_experiment_registry(self) -> dict[str, Any]:
        experiments_file = self.eval_base / "experiments.json"
        if experiments_file.exists():
            try:
                return json.loads(experiments_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def update_experiment_registry(
        self,
        strategy_id: str,
        results_summary: dict[str, Any],
    ) -> None:
        experiments_file = self.eval_base / "experiments.json"
        existing = {}
        if experiments_file.exists():
            try:
                existing = json.loads(experiments_file.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        existing[strategy_id] = results_summary
        experiments_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    def save_judge_results(self, strategy_id: str, judge_data: dict[str, Any]) -> None:
        j_file = self.eval_base / "judge_results.json"
        existing = {}
        if j_file.exists():
            try:
                existing = json.loads(j_file.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        existing[strategy_id] = judge_data
        j_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    def record_failure(
        self, strategy_id: str, document_id: str, error_msg: str
    ) -> None:
        f_file = self.eval_base / "failures.json"
        failures = []
        if f_file.exists():
            try:
                failures = json.loads(f_file.read_text(encoding="utf-8"))
            except Exception:
                failures = []

        failures.append(
            {
                "strategy_id": strategy_id,
                "document_id": document_id,
                "error": error_msg,
            }
        )
        f_file.write_text(json.dumps(failures, indent=2), encoding="utf-8")
