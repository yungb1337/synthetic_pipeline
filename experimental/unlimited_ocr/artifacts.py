"""Artifact storage and layout manager for Unlimited-OCR evaluation.

Ensures strict separation from production/parsed stores:
artifacts/unlimited_ocr_eval/<doc_id>/
  source_metadata.json
  raw_output.json
  raw_output.md
  normalized_output.json
  metrics.json
  runtime.json
  logs/stdout.log
  logs/stderr.log

evaluation/unlimited_ocr/
  manifest.json
  results.json
  judge_results.json
  report.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import UnlimitedOCRConfig


class ArtifactManager:
    def __init__(self, config: UnlimitedOCRConfig):
        self.config = config
        self.artifacts_dir = Path(config.artifacts_dir)
        self.evaluation_dir = Path(config.evaluation_dir)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.evaluation_dir.mkdir(parents=True, exist_ok=True)

    def get_doc_dir(self, doc_id: str) -> Path:
        d = self.artifacts_dir / doc_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "logs").mkdir(parents=True, exist_ok=True)
        return d

    def save_source_metadata(self, doc_id: str, metadata: dict[str, Any]) -> Path:
        doc_dir = self.get_doc_dir(doc_id)
        p = doc_dir / "source_metadata.json"
        p.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return p

    def save_raw_output(
        self, doc_id: str, raw_data: dict[str, Any], raw_markdown: str
    ) -> tuple[Path, Path]:
        doc_dir = self.get_doc_dir(doc_id)
        p_json = doc_dir / "raw_output.json"
        p_md = doc_dir / "raw_output.md"
        p_json.write_text(
            json.dumps(raw_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        p_md.write_text(raw_markdown, encoding="utf-8")
        return p_json, p_md

    def save_normalized_dom(self, doc_id: str, dom_dict: dict[str, Any]) -> Path:
        doc_dir = self.get_doc_dir(doc_id)
        p = doc_dir / "normalized_output.json"
        p.write_text(
            json.dumps(dom_dict, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return p

    def save_metrics(self, doc_id: str, metrics_dict: dict[str, Any]) -> Path:
        doc_dir = self.get_doc_dir(doc_id)
        p = doc_dir / "metrics.json"
        p.write_text(
            json.dumps(metrics_dict, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return p

    def save_runtime(self, doc_id: str, runtime_info: dict[str, Any]) -> Path:
        doc_dir = self.get_doc_dir(doc_id)
        p = doc_dir / "runtime.json"
        p.write_text(
            json.dumps(runtime_info, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return p

    def save_logs(
        self, doc_id: str, stdout_text: str, stderr_text: str
    ) -> tuple[Path, Path]:
        doc_dir = self.get_doc_dir(doc_id)
        p_out = doc_dir / "logs" / "stdout.log"
        p_err = doc_dir / "logs" / "stderr.log"
        p_out.write_text(stdout_text, encoding="utf-8")
        p_err.write_text(stderr_text, encoding="utf-8")
        return p_out, p_err

    def save_evaluation_manifest(self, manifest: list[dict[str, Any]]) -> Path:
        self.evaluation_dir.mkdir(parents=True, exist_ok=True)
        p = self.evaluation_dir / "manifest.json"
        p.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return p

    def save_evaluation_results(self, results: list[dict[str, Any]]) -> Path:
        self.evaluation_dir.mkdir(parents=True, exist_ok=True)
        p = self.evaluation_dir / "results.json"
        p.write_text(
            json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return p

    def save_judge_results(self, judge_results: dict[str, Any]) -> Path:
        self.evaluation_dir.mkdir(parents=True, exist_ok=True)
        p = self.evaluation_dir / "judge_results.json"
        p.write_text(
            json.dumps(judge_results, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return p

    def save_summary_report(self, report_dict: dict[str, Any]) -> Path:
        self.evaluation_dir.mkdir(parents=True, exist_ok=True)
        p = self.evaluation_dir / "report.json"
        p.write_text(
            json.dumps(report_dict, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return p
