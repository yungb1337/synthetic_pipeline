"""Unified Live Streaming Evaluation & Auto-Escalation Pipeline for pdf-inspector.
Runs concurrent hybrid parsing + live LLM Judge scoring + auto-retry on low scores.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psutil

from experimental.pdf_inspector_eval.dom_converter import PDFInspectorDOMConverter
from experimental.pdf_inspector_eval.inspector_adapter import PDFInspectorAdapter
from experimental.pdf_inspector_eval.smart_router import PDFInspectorSmartRouter
from experimental.table_eval.judge_evaluator import TableBenchmarkJudgeEvaluator


class LiveStreamingPipeline:
    """End-to-end streaming parsing + live LLM Judge scoring with auto-retry escalation."""

    def __init__(
        self,
        output_dir: str | Path = "artifacts/pdf_inspector_eval/run_1000",
        corpus_type: str = "corpus_b",
        judge_workers: int = 4,
        judge_model: str = "gemini-3.5-flash-lite",
        score_threshold: float = 0.75,
    ):
        self.output_dir = Path(output_dir).resolve()
        self.normalized_dir = self.output_dir / "normalized_output"
        self.raw_dir = self.output_dir / "raw_output"
        self.judgment_dir = self.output_dir / "judgment"
        self.escalation_dir = self.output_dir / "escalated_rejudgments"

        self.normalized_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.judgment_dir.mkdir(parents=True, exist_ok=True)
        self.escalation_dir.mkdir(parents=True, exist_ok=True)

        if corpus_type == "corpus_b":
            self.pdf_dir = (
                ROOT_DIR
                / "checkpoints"
                / "run"
                / "run-2026-09-14-eval-1000"
                / "sources"
                / "pdf"
            )
        elif corpus_type == "corpus_945":
            self.pdf_dir = (
                ROOT_DIR
                / "checkpoints"
                / "run"
                / "run-2026-09-04-parser-reliability"
                / "sources"
                / "pdf"
            )
        elif corpus_type in ("curated_hard", "curated_easy"):
            self.pdf_dir = ROOT_DIR / "artifacts" / corpus_type
        else:
            self.pdf_dir = Path(corpus_type).resolve()

        self.router = PDFInspectorSmartRouter()
        self.adapter = PDFInspectorAdapter()
        self.converter = PDFInspectorDOMConverter()
        self.judge_evaluator = TableBenchmarkJudgeEvaluator(
            model=judge_model, pacing_seconds=0.5
        )
        self.judge_workers = judge_workers
        self.judge_model = judge_model
        self.score_threshold = score_threshold
        self.process = psutil.Process()

    def discover_targets(self, limit: int | None = None) -> list[dict[str, Any]]:
        all_pdfs = sorted(glob.glob(str(self.pdf_dir / "**" / "*.pdf"), recursive=True))
        targets = [
            {"doc_id": Path(p).stem, "pdf_path": str(Path(p).resolve())}
            for p in all_pdfs
        ]
        if limit:
            targets = targets[:limit]
        return targets

    def parse_single_doc(
        self,
        doc_id: str,
        pdf_path: Path,
        force_docling_pages: list[int] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        """Parses a document with per-page hybrid routing and returns the DOM path and telemetry."""
        doc_t0 = time.perf_counter()
        out_dom_path = self.normalized_dir / f"{doc_id}.parsed.v1.docJSON"
        out_telemetry_path = self.raw_dir / f"{doc_id}.runtime.json"

        # 1. Routing & Extraction
        decision = self.router.route_document(pdf_path)
        ext_result = self.adapter.inspect_and_extract(pdf_path, doc_id=doc_id)

        # Apply forced escalation on specific pages if requested by retry loop
        if force_docling_pages:
            for p in ext_result.pages:
                if (
                    p.page_num in force_docling_pages
                    or (p.page_num - 1) in force_docling_pages
                ):
                    p.route = "docling_heavy"

        # 2. Build DOM
        dom = self.converter.build_canonical_dom(ext_result)
        doc_elapsed = (time.perf_counter() - doc_t0) * 1000.0
        page_count = len(dom.pages)

        # 3. Save DOM
        dom_dict = dom.model_dump()
        out_dom_path.write_text(json.dumps(dom_dict, indent=2), encoding="utf-8")

        # 4. Save Telemetry
        mem_info = self.process.memory_info()
        telemetry = {
            "doc_id": doc_id,
            "pdf_path": str(pdf_path),
            "status": "ok",
            "route": decision.route,
            "page_count": page_count,
            "route_breakdown": ext_result.route_breakdown,
            "num_blocks": dom.num_blocks(),
            "num_tables": dom.num_tables(),
            "num_references": len(dom.references),
            "elapsed_ms": doc_elapsed,
            "pages_per_sec": (page_count / (doc_elapsed / 1000.0))
            if doc_elapsed > 0
            else 0.0,
            "ram_rss_gb": round(mem_info.rss / (1024**3), 3),
        }
        out_telemetry_path.write_text(json.dumps(telemetry, indent=2), encoding="utf-8")
        return out_dom_path, telemetry

    def _judge_task(
        self,
        doc_id: str,
        dom_path: Path,
        pdf_path: Path,
        out_path: Path,
        resume: bool = True,
    ) -> dict[str, Any]:
        if resume and out_path.exists():
            try:
                cached = json.loads(out_path.read_text(encoding="utf-8"))
                return {"doc_id": doc_id, "cached": True, "data": cached}
            except Exception:
                pass
        res = self.judge_evaluator.evaluate_doc(pdf_path, dom_path, out_path)
        return {"doc_id": doc_id, "cached": False, "data": res}

    def run_streaming(
        self, limit: int | None = None, resume: bool = True
    ) -> dict[str, Any]:
        targets = self.discover_targets(limit=limit)
        total_docs = len(targets)

        print("=" * 70)
        print(f"Starting Live Streaming Hybrid Pipeline ({total_docs} Documents)")
        print(f"Corpus: {self.pdf_dir.name} | Output: {self.output_dir}")
        print(f"Judge Workers: {self.judge_workers} | Model: {self.judge_model}")
        print("=" * 70)

        t_pipeline_start = time.perf_counter()
        total_pages_parsed = 0
        route_totals = {"rust_native": 0, "cuda_ocr": 0, "docling_heavy": 0}

        verdicts_count = {"PASS": 0, "PASS_WITH_ISSUES": 0, "FAIL": 0}
        metrics_sum = {
            "completeness": 0.0,
            "fidelity": 0.0,
            "structure": 0.0,
            "tables": 0.0,
            "references": 0.0,
            "scans_ocr": 0.0,
        }
        metric_counts = {k: 0 for k in metrics_sum}
        low_scoring_docs: list[dict[str, Any]] = []

        judge_futures = {}
        with ThreadPoolExecutor(max_workers=self.judge_workers) as judge_pool:
            # 1. Pipeline loop: parse doc and immediately submit to judge pool
            for idx, tgt in enumerate(targets, 1):
                doc_id = tgt["doc_id"]
                pdf_path = Path(tgt["pdf_path"])
                out_verdict_path = self.judgment_dir / f"{doc_id}.verdict.json"

                try:
                    dom_path, telem = self.parse_single_doc(doc_id, pdf_path)
                    total_pages_parsed += telem["page_count"]
                    for r_type, count in telem.get("route_breakdown", {}).items():
                        route_totals[r_type] = route_totals.get(r_type, 0) + count

                    p_sec = telem["pages_per_sec"]
                    print(
                        f"[{idx:4d}/{total_docs:4d}] [PARSED] {doc_id} ({telem['page_count']} pgs, {telem['elapsed_ms']:.1f}ms, {p_sec:.1f} p/s)"
                    )

                    # Submit to judge
                    fut = judge_pool.submit(
                        self._judge_task,
                        doc_id,
                        dom_path,
                        pdf_path,
                        out_verdict_path,
                        resume,
                    )
                    judge_futures[fut] = (doc_id, dom_path, pdf_path, out_verdict_path)

                except Exception as exc:
                    print(f"[{idx:4d}/{total_docs:4d}] [PARSE ERROR] {doc_id}: {exc}")

            print("\n>>> All documents parsed. Gathering live judgment stream...\n")

            # 2. Collect and stream judgments live
            completed_judgments = 0
            for fut in as_completed(judge_futures):
                doc_id, dom_path, pdf_path, out_verdict_path = judge_futures[fut]
                completed_judgments += 1
                try:
                    res = fut.result()
                    data = res.get("data") or {}
                    if not data:
                        # Judge was skipped or rate-limited
                        print(
                            f"[{completed_judgments:4d}/{total_docs:4d}] [JUDGE SKIPPED] {doc_id}"
                        )
                        continue

                    v_raw = data.get("verdict_status") or data.get("verdict")
                    if isinstance(v_raw, dict):
                        v_stat = str(v_raw.get("verdict", "UNKNOWN")).upper()
                        m = v_raw.get("metrics", {})
                    else:
                        v_stat = str(v_raw or "UNKNOWN").upper()
                        m = data.get("metrics", {})
                        if (
                            not m
                            and "verdict" in data
                            and isinstance(data["verdict"], dict)
                        ):
                            m = data["verdict"].get("metrics", {})

                    if "PASS_WITH_ISSUES" in v_stat:
                        verdicts_count["PASS_WITH_ISSUES"] += 1
                    elif "PASS" in v_stat:
                        verdicts_count["PASS"] += 1
                    elif "FAIL" in v_stat:
                        verdicts_count["FAIL"] += 1

                    has_low_metric = False
                    if isinstance(m, dict):
                        for k in metrics_sum:
                            if k in m and m[k] is not None:
                                try:
                                    val = float(m[k])
                                    metrics_sum[k] += val
                                    metric_counts[k] += 1
                                    if val < self.score_threshold and k not in (
                                        "tables",
                                        "scans_ocr",
                                    ):
                                        has_low_metric = True
                                except Exception:
                                    pass

                    cached_lbl = "[CACHED]" if res.get("cached") else "[JUDGED]"
                    c_score = (
                        m.get("completeness", "N/A") if isinstance(m, dict) else "N/A"
                    )
                    t_score = m.get("tables", "N/A") if isinstance(m, dict) else "N/A"
                    print(
                        f"[{completed_judgments:4d}/{total_docs:4d}] {cached_lbl} {doc_id} -> {v_stat} (Compl: {c_score}, Tables: {t_score})"
                    )

                    if v_stat == "FAIL" or has_low_metric:
                        low_scoring_docs.append(
                            {
                                "doc_id": doc_id,
                                "pdf_path": pdf_path,
                                "metrics": m,
                                "verdict": v_stat,
                            }
                        )

                    # Live Scorecard every 10 docs
                    if (
                        completed_judgments % 10 == 0
                        or completed_judgments == total_docs
                    ):
                        cur_averages = {
                            k: f"{(metrics_sum[k] / metric_counts[k] * 100):.1f}%"
                            if metric_counts[k] > 0
                            else "0.0%"
                            for k in metrics_sum
                        }
                        print(
                            f"--- [LIVE SCORECARD (n={completed_judgments}/{total_docs})] PASS: {verdicts_count['PASS']} | ISSUES: {verdicts_count['PASS_WITH_ISSUES']} | FAIL: {verdicts_count['FAIL']} | Struct: {cur_averages['structure']} | Compl: {cur_averages['completeness']} | Fmt: {cur_averages['fidelity']} ---"
                        )

                        # Write intermediate summary
                        inter_averages = {
                            k: round((metrics_sum[k] / metric_counts[k]), 3)
                            if metric_counts[k] > 0
                            else 0.0
                            for k in metrics_sum
                        }
                        inter_summary = {
                            "total_documents": completed_judgments,
                            "total_pages": total_pages_parsed,
                            "route_totals": route_totals,
                            "verdicts": verdicts_count,
                            "metrics_mean": inter_averages,
                            "metrics_percentage": {
                                k: f"{v * 100:.1f}%" for k, v in inter_averages.items()
                            },
                        }
                        (self.judgment_dir / "pipeline_summary.json").write_text(
                            json.dumps(inter_summary, indent=2), encoding="utf-8"
                        )

                except Exception as exc:
                    print(
                        f"[{completed_judgments:4d}/{total_docs:4d}] [JUDGE ERROR] {doc_id}: {exc}"
                    )

        # 3. Auto-Retry Escalation Loop on Failed / Low-Scoring Docs
        if low_scoring_docs:
            print("\n" + "=" * 70)
            print(
                f">>> Auto-Retry Escalation on {len(low_scoring_docs)} Low-Scoring Documents..."
            )
            print("=" * 70)

            for l_item in low_scoring_docs:
                d_id = l_item["doc_id"]
                p_path = l_item["pdf_path"]

                # Identify specific target pages for escalation (tables, complex columns, OCR)
                ext_peek = self.adapter.inspect_and_extract(p_path, doc_id=d_id)
                target_pages = [
                    p.page_num - 1
                    for p in ext_peek.pages
                    if p.has_tables or p.has_columns or p.needs_ocr
                ]
                if not target_pages:
                    target_pages = [0]

                print(
                    f"[ESCALATE] Re-parsing {d_id} with single-page Docling escalation on pages {target_pages}..."
                )
                try:
                    import gc

                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    gc.collect()

                    esc_dom_path, _ = self.parse_single_doc(
                        d_id, p_path, force_docling_pages=target_pages
                    )
                    esc_verdict_path = self.escalation_dir / f"{d_id}.verdict.json"
                    new_res = self.judge_evaluator.evaluate_doc(
                        p_path, esc_dom_path, esc_verdict_path
                    )
                    if new_res:
                        v_obj = new_res.get("verdict_status") or new_res.get("verdict")
                        if isinstance(v_obj, dict):
                            new_v = str(v_obj.get("verdict", "UNKNOWN")).upper()
                        else:
                            new_v = str(v_obj or "UNKNOWN").upper()
                        print(f"[RESOLVED] {d_id} re-judged -> {new_v}")
                    else:
                        print(f"[ESCALATED] {d_id} re-parsed (judge skipped/deferred)")
                except Exception as exc:
                    print(f"[ESCALATION ERROR] {d_id}: {exc}")

        total_wall_s = time.perf_counter() - t_pipeline_start
        averages = {
            k: round((metrics_sum[k] / metric_counts[k]), 3)
            if metric_counts[k] > 0
            else 0.0
            for k in metrics_sum
        }

        summary = {
            "total_documents": total_docs,
            "total_pages": total_pages_parsed,
            "total_wall_seconds": round(total_wall_s, 2),
            "effective_throughput_pages_per_sec": round(
                (total_pages_parsed / total_wall_s), 2
            )
            if total_wall_s > 0
            else 0.0,
            "route_totals": route_totals,
            "verdicts": verdicts_count,
            "metrics_mean": averages,
            "metrics_percentage": {k: f"{v * 100:.1f}%" for k, v in averages.items()},
        }

        summary_path = self.judgment_dir / "pipeline_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        print("\n" + "=" * 70)
        print("FINAL PIPELINE SCORECARD")
        print(
            f"Total Documents: {total_docs} | Pages: {total_pages_parsed} | Wall Time: {total_wall_s:.1f}s ({total_wall_s / 60.0:.2f} min)"
        )
        print(f"Throughput: {summary['effective_throughput_pages_per_sec']} pages/sec")
        print(
            f"Route Totals (Pages): Native: {route_totals.get('rust_native', 0)} | Docling: {route_totals.get('docling_heavy', 0)} | OCR: {route_totals.get('cuda_ocr', 0)}"
        )
        print(
            f"Verdicts: PASS: {verdicts_count['PASS']} | PASS_WITH_ISSUES: {verdicts_count['PASS_WITH_ISSUES']} | FAIL: {verdicts_count['FAIL']}"
        )
        print("Metrics:")
        for k, v in averages.items():
            print(f"  - {k.capitalize()}: {v * 100:.1f}%")
        print("=" * 70)

        return summary


def main():
    parser = argparse.ArgumentParser(description="Run Live Streaming Hybrid Pipeline")
    parser.add_argument(
        "--corpus", default="corpus_b", help="Corpus name ('corpus_b' or 'corpus_945')"
    )
    parser.add_argument("--output-dir", default=None, help="Output directory")
    parser.add_argument("--limit", type=int, default=None, help="Document limit")
    parser.add_argument(
        "--workers", type=int, default=4, help="Concurrent judge workers"
    )
    parser.add_argument(
        "--model", default="gemini-3.5-flash-lite", help="LLM Judge model"
    )
    parser.add_argument(
        "--no-resume", action="store_true", help="Re-run without using cache"
    )
    args = parser.parse_args()

    out_dir = args.output_dir
    if not out_dir:
        if args.corpus == "corpus_b":
            out_dir = "artifacts/pdf_inspector_eval/corpus_b_1000"
        elif args.corpus == "corpus_945":
            out_dir = "artifacts/pdf_inspector_eval/corpus_945"
        elif args.corpus == "curated_hard":
            out_dir = "artifacts/pdf_inspector_eval/curated_hard"
        elif args.corpus == "curated_easy":
            out_dir = "artifacts/pdf_inspector_eval/curated_easy"
        else:
            out_dir = "artifacts/pdf_inspector_eval/custom_run"

    pipeline = LiveStreamingPipeline(
        output_dir=out_dir,
        corpus_type=args.corpus,
        judge_workers=args.workers,
        judge_model=args.model,
    )
    pipeline.run_streaming(limit=args.limit, resume=not args.no_resume)


if __name__ == "__main__":
    main()
