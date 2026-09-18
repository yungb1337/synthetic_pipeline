"""Concurrent Streaming LLM Judge Evaluator for pdf-inspector extraction DOMs.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from experimental.table_eval.judge_evaluator import TableBenchmarkJudgeEvaluator


class PDFInspectorStreamJudge:
    """Evaluates extracted DOMs with multi-worker concurrent LLM Judge."""

    def __init__(
        self,
        artifact_dir: str | Path = "artifacts/pdf_inspector_eval/corpus_b",
        corpus_type: str = "corpus_b",
        max_workers: int = 4,
        model: str = "gemini-3.5-flash-lite",
    ):
        self.artifact_dir = Path(artifact_dir).resolve()
        self.normalized_dir = self.artifact_dir / "normalized_output"
        self.judgment_dir = self.artifact_dir / "judgment"
        self.judgment_dir.mkdir(parents=True, exist_ok=True)

        if corpus_type == "corpus_b":
            self.pdf_dir = ROOT_DIR / "checkpoints" / "run" / "run-2026-09-14-eval-1000" / "sources" / "pdf"
        elif corpus_type == "corpus_945":
            self.pdf_dir = ROOT_DIR / "checkpoints" / "run" / "run-2026-09-04-parser-reliability" / "sources" / "pdf"
        elif corpus_type in ("curated_hard", "curated_easy"):
            self.pdf_dir = ROOT_DIR / "artifacts" / corpus_type
        else:
            self.pdf_dir = Path(corpus_type).resolve()

        self.max_workers = max_workers
        self.model = model
        self.evaluator = TableBenchmarkJudgeEvaluator(model=model, pacing_seconds=0.5)

    def _judge_task(self, doc_id: str, dom_path: Path, pdf_path: Path, out_path: Path, resume: bool = True) -> dict[str, Any]:
        if resume and out_path.exists():
            try:
                cached = json.loads(out_path.read_text(encoding="utf-8"))
                return {"doc_id": doc_id, "cached": True, "data": cached}
            except Exception:
                pass

        res = self.evaluator.evaluate_doc(pdf_path, dom_path, out_path)
        return {"doc_id": doc_id, "cached": False, "data": res}

    def run(self, max_eval: int | None = None, resume: bool = True) -> dict[str, Any]:
        print("=" * 60)
        print(f"Starting Streaming LLM Judge ({self.max_workers} Workers, Model: {self.model})")
        print(f"DOM Directory: {self.normalized_dir}")
        print(f"Judgment Directory: {self.judgment_dir}")
        print("=" * 60)

        dom_files = sorted(glob.glob(str(self.normalized_dir / "*.parsed.v1.docJSON")))
        if max_eval:
            dom_files = dom_files[:max_eval]

        tasks = []
        for dom_p in dom_files:
            dom_path = Path(dom_p)
            doc_id = dom_path.stem.replace(".parsed.v1", "")
            pdf_path = self.pdf_dir / f"{doc_id}.pdf"
            if not pdf_path.exists():
                alt = list(self.pdf_dir.glob(f"**/{doc_id}.pdf"))
                if alt:
                    pdf_path = alt[0]
                else:
                    alt_stem = list(self.pdf_dir.glob(f"**/{doc_id}*"))
                    if alt_stem:
                        pdf_path = alt_stem[0]
            if pdf_path.exists():
                out_path = self.judgment_dir / f"{doc_id}.verdict.json"
                tasks.append((doc_id, dom_path, pdf_path, out_path))

        total_tasks = len(tasks)
        print(f"Identified {total_tasks} documents ready for LLM Judge scoring.")

        completed = 0
        verdicts = {"PASS": 0, "PASS_WITH_ISSUES": 0, "FAIL": 0}
        metrics_sum = {
            "completeness": 0.0,
            "fidelity": 0.0,
            "structure": 0.0,
            "tables": 0.0,
            "references": 0.0,
            "scans_ocr": 0.0,
        }
        metric_counts = {k: 0 for k in metrics_sum}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._judge_task, doc_id, dom_path, pdf_path, out_path, resume): doc_id
                for doc_id, dom_path, pdf_path, out_path in tasks
            }

            for fut in as_completed(futures):
                doc_id = futures[fut]
                completed += 1
                try:
                    res = fut.result()
                    data = res.get("data", {})

                    # Extract verdict
                    v_raw = data.get("verdict_status") or data.get("verdict")
                    if isinstance(v_raw, dict):
                        v_stat_str = str(v_raw.get("verdict", "UNKNOWN")).upper()
                        m = v_raw.get("metrics", {})
                    else:
                        v_stat_str = str(v_raw).upper()
                        m = data.get("metrics", {})
                        if not m and "verdict" in data and isinstance(data["verdict"], dict):
                            m = data["verdict"].get("metrics", {})

                    if "PASS_WITH_ISSUES" in v_stat_str:
                        verdicts["PASS_WITH_ISSUES"] += 1
                    elif "PASS" in v_stat_str:
                        verdicts["PASS"] += 1
                    elif "FAIL" in v_stat_str:
                        verdicts["FAIL"] += 1

                    if isinstance(m, dict):
                        for k in metrics_sum:
                            if k in m and m[k] is not None:
                                try:
                                    val = float(m[k])
                                    metrics_sum[k] += val
                                    metric_counts[k] += 1
                                except Exception:
                                    pass

                    cached_label = "[CACHED]" if res.get("cached") else "[NEW]"
                    t_score = m.get("tables", "N/A") if isinstance(m, dict) else "N/A"
                    c_score = m.get("completeness", "N/A") if isinstance(m, dict) else "N/A"
                    print(f"[{completed:4d}/{total_tasks:4d}] {cached_label} {doc_id} -> {v_stat_str} (Compl: {c_score}, Tables: {t_score})")

                except Exception as exc:
                    print(f"[{completed:4d}/{total_tasks:4d}] {doc_id} -> ERROR ({exc})")

        # Compute averages
        averages = {}
        for k, total in metrics_sum.items():
            cnt = metric_counts[k]
            averages[k] = round((total / cnt), 3) if cnt > 0 else 0.0

        # Calculate Table accuracy for docs containing tables
        table_present_scores = []
        for dom_p in dom_files:
            doc_id = Path(dom_p).stem.replace(".parsed.v1", "")
            out_path = self.judgment_dir / f"{doc_id}.verdict.json"
            if out_path.exists():
                try:
                    v_data = json.loads(out_path.read_text(encoding="utf-8"))
                    d_sum = v_data.get("dom_summary", {})
                    t_val = v_data.get("verdict", {}).get("metrics", {}).get("tables")
                    if d_sum.get("tables_total", 0) > 0 and t_val is not None:
                        table_present_scores.append(float(t_val))
                except Exception:
                    pass

        table_evaluable_avg = round(sum(table_present_scores) / len(table_present_scores), 3) if table_present_scores else 0.0

        summary = {
            "total_judged": completed,
            "verdicts": verdicts,
            "metrics_mean": averages,
            "metrics_percentage": {k: f"{v * 100:.1f}%" for k, v in averages.items()},
            "table_accuracy_evaluable": f"{table_evaluable_avg * 100:.1f}%" if table_present_scores else "N/A",
        }

        summary_path = self.judgment_dir / "judgment_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        print("\n" + "=" * 60)
        print("LLM Judge Summary Scorecard")
        print(f"Total Judged: {completed}")
        print(f"Verdicts: PASS: {verdicts['PASS']} | PASS_WITH_ISSUES: {verdicts['PASS_WITH_ISSUES']} | FAIL: {verdicts['FAIL']}")
        print("Average Metrics:")
        for k, v in averages.items():
            print(f"  - {k.capitalize()}: {v * 100:.1f}%")
        if table_present_scores:
            print(f"  - Table Accuracy (Docs with Tables, n={len(table_present_scores)}): {table_evaluable_avg * 100:.1f}%")
        print("=" * 60)

        return summary


def main():
    parser = argparse.ArgumentParser(description="Run concurrent streaming LLM judge")
    parser.add_argument("--artifact-dir", default="artifacts/pdf_inspector_eval/corpus_b", help="Artifacts directory containing normalized_output")
    parser.add_argument("--corpus", default="corpus_b", help="Corpus type ('corpus_b' or 'corpus_945')")
    parser.add_argument("--workers", type=int, default=4, help="Number of concurrent judge threads")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of documents to judge")
    parser.add_argument("--model", default="gemini-3.5-flash-lite", help="Gemini judge model")
    parser.add_argument("--no-resume", action="store_true", help="Do not use cached verdicts; re-run all judgments")
    args = parser.parse_args()

    judge = PDFInspectorStreamJudge(
        artifact_dir=args.artifact_dir,
        corpus_type=args.corpus,
        max_workers=args.workers,
        model=args.model,
    )
    judge.run(max_eval=args.limit, resume=not args.no_resume)


if __name__ == "__main__":
    main()
