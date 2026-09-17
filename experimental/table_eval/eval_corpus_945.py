"""Batch evaluation pipeline running P003 against Corpus 945 (PMC Open Access) with LLM judge scoring.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import gc
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import fitz

# Ensure workspace root is on sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from experimental.table_eval.config import BenchmarkConfig, PERMUTATIONS
from experimental.table_eval.converter import TableBenchmarkDOMConverter
from experimental.table_eval.judge_evaluator import TableBenchmarkJudgeEvaluator
from experimental.table_eval.profiler import HardwareProfiler
from experimental.table_eval.strategies import ExecutionStrategy

SOURCES_DIR = WORKSPACE_ROOT / "checkpoints" / "run" / "run-2026-09-14-full-corpus" / "parsed" / "raw"
JUDGMENTS_DIR = WORKSPACE_ROOT / "checkpoints" / "run" / "run-2026-09-14-full-corpus" / "judgment"
OUTPUT_BASE = WORKSPACE_ROOT / "artifacts" / "table_eval" / "corpus_945_p003"
EVAL_DIR = WORKSPACE_ROOT / "evaluation" / "table_benchmark"


def _judge_task(task: dict[str, Any]) -> dict[str, Any]:
    doc_id = task["doc_id"]
    pdf_path = task["pdf_path"]
    dom_path = task["dom_path"]
    out_verdict_path = Path(task["out_verdict_path"])
    pacing = task.get("pacing", 1.0)
    model = task.get("model", "gemini-3.5-flash-lite")

    if out_verdict_path.exists() and task.get("resume", False):
        try:
            data = json.loads(out_verdict_path.read_text(encoding="utf-8"))
            if "verdict" in data and isinstance(data["verdict"], dict):
                v_obj = data["verdict"]
                if "metrics" in v_obj:
                    data["metrics"] = v_obj["metrics"]
                if "verdict" in v_obj:
                    data["verdict_status"] = v_obj["verdict"]
            return {"doc_id": doc_id, "data": data, "cached": True}
        except Exception:
            pass

    judge = TableBenchmarkJudgeEvaluator(model=model, pacing_seconds=pacing)
    out_verdict_path.parent.mkdir(parents=True, exist_ok=True)
    res = judge.evaluate_doc(pdf_path, dom_path, out_verdict_path)
    return {"doc_id": doc_id, "data": res, "cached": False}


class Corpus945BatchEvaluator:
    """Manages full batch evaluation of Corpus 945 using P003."""

    def __init__(
        self,
        strategy_id: str = "P003",
        judge_workers: int = 4,
        render_dpi: int = 150,
        judge_model: str = "gemini-3.5-flash-lite",
    ):
        self.strategy_id = strategy_id
        self.judge_workers = judge_workers
        self.render_dpi = render_dpi
        self.judge_model = judge_model

        self.normalized_dir = OUTPUT_BASE / "normalized_output"
        self.raw_dir = OUTPUT_BASE / "raw_output"
        self.logs_dir = OUTPUT_BASE / "logs"

        self.normalized_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        EVAL_DIR.mkdir(parents=True, exist_ok=True)

    def discover_target_documents(self, limit: int = 0) -> list[dict[str, Any]]:
        """Discovers all 945 raw PDFs and associates them with historical judgments."""
        raw_files = sorted(SOURCES_DIR.glob("*.pdf"))
        targets = []
        for p in raw_files:
            doc_id = f"d-{p.stem[:16]}"
            prev_file = JUDGMENTS_DIR / f"{doc_id}.json"
            targets.append({
                "doc_id": doc_id,
                "pdf_path": str(p),
                "prev_verdict_file": str(prev_file) if prev_file.exists() else None,
            })
        return targets[:limit] if limit > 0 else targets

    def run_batch_extraction(
        self,
        targets: list[dict[str, Any]],
        resume: bool = True,
    ) -> list[dict[str, Any]]:
        """Executes warm single-process GPU extraction on all 945 documents."""
        print(f"\n==================================================")
        print(f"Phase 1: P003 Hybrid Extraction — {len(targets)} Documents (Corpus 945)")
        print(f"Strategy: {self.strategy_id} | OCR Backend: PP-OCRv6 CUDA | Mode: Persistent GPU Pipeline")
        print(f"==================================================")

        spec = PERMUTATIONS.get(self.strategy_id, PERMUTATIONS["P003"])
        strategy = ExecutionStrategy(spec)
        converter = TableBenchmarkDOMConverter()
        profiler = HardwareProfiler()

        results = []
        total_pages = 0
        total_tables = 0
        failures = 0
        t0 = time.perf_counter()

        for idx, task in enumerate(targets, 1):
            pdf_path = Path(task["pdf_path"])
            doc_id = task["doc_id"]
            out_dom_path = self.normalized_dir / f"{doc_id}.parsed.v1.docJSON"
            out_telemetry_path = self.raw_dir / f"{doc_id}.telemetry.json"

            if out_dom_path.exists() and resume:
                try:
                    runtime_data = json.loads(out_telemetry_path.read_text(encoding="utf-8"))
                    results.append(runtime_data)
                    total_pages += runtime_data.get("page_count", 0)
                    total_tables += runtime_data.get("tables_extracted", 0)
                    print(f"[{idx:3d}/{len(targets)}] {doc_id} ({pdf_path.name[:24]}...) ... (cached: {runtime_data.get('page_count',0)} pgs, {runtime_data.get('tables_extracted',0)} tbls)", flush=True)
                    continue
                except Exception:
                    pass

            t0_wall = time.perf_counter()
            prof = profiler.start_profile()

            page_results = []
            doc_tables_count = 0
            status = "success"
            error_msg = None
            page_count = 0

            try:
                source_bytes = pdf_path.read_bytes()
                source_sha = hashlib.sha256(source_bytes).hexdigest()

                with fitz.open(pdf_path) as doc:
                    page_count = doc.page_count
                    for pno in range(page_count):
                        page = doc[pno]
                        p_res = strategy.process_page(
                            page,
                            pno,
                            str(pdf_path),
                            render_dpi=self.render_dpi,
                        )
                        page_results.append(p_res)
                        doc_tables_count += len(p_res.get("tables", []))

                t_dom0 = time.perf_counter()
                canonical_dom = converter.build_canonical_dom(
                    document_id=doc_id,
                    source_sha256=source_sha,
                    page_results=page_results,
                    strategy_id=strategy.spec.id,
                )
                dom_ms = (time.perf_counter() - t_dom0) * 1000.0

                out_dom_path.parent.mkdir(parents=True, exist_ok=True)
                out_dom_path.write_text(canonical_dom.model_dump_json(indent=2), encoding="utf-8")

            except Exception as exc:
                status = "failed"
                error_msg = str(exc)
                dom_ms = 0.0

            total_wall_ms = (time.perf_counter() - t0_wall) * 1000.0
            prof.dom_conversion_ms = dom_ms
            prof = profiler.finalize_profile(prof, total_wall_ms)

            runtime_data = {
                "strategy_id": strategy.spec.id,
                "document_id": doc_id,
                "pdf_path": str(pdf_path),
                "page_count": page_count,
                "tables_extracted": doc_tables_count,
                "status": status,
                "error": error_msg,
                "telemetry": prof.to_dict(),
                "dom_path": str(out_dom_path) if status == "success" else None,
            }

            out_telemetry_path.parent.mkdir(parents=True, exist_ok=True)
            out_telemetry_path.write_text(json.dumps(runtime_data, indent=2), encoding="utf-8")

            results.append(runtime_data)
            total_pages += page_count
            total_tables += doc_tables_count

            if status == "failed":
                failures += 1
                print(f"[{idx:3d}/{len(targets)}] {doc_id} ({pdf_path.name[:24]}...) ... FAILED ({error_msg})", flush=True)
            else:
                pps = (page_count / (total_wall_ms / 1000.0)) if total_wall_ms > 0 else 0.0
                print(f"[{idx:3d}/{len(targets)}] {doc_id} ({pdf_path.name[:24]}...) ... OK ({page_count} pgs, {doc_tables_count} tbls, {total_wall_ms:.0f}ms, {pps:.2f} p/s)", flush=True)

        wall_time_s = time.perf_counter() - t0
        pages_per_sec = total_pages / wall_time_s if wall_time_s > 0 else 0.0

        print(f"\n--- Phase 1 Complete ---")
        print(f"Processed: {len(results)} docs, {total_pages} pages, {total_tables} tables in {wall_time_s:.1f}s")
        print(f"Effective Throughput: {pages_per_sec:.3f} pages/sec")
        print(f"Failures: {failures}", flush=True)
        return results

    def run_batch_judgment(
        self,
        targets: list[dict[str, Any]],
        resume: bool = True,
        pacing: float = 1.0,
    ) -> list[dict[str, Any]]:
        """Runs concurrent LLM Judge evaluations against generated DOMs."""
        print(f"\n==================================================")
        print(f"Phase 2: LLM Judge Evaluation — {len(targets)} Documents (Corpus 945)")
        print(f"Model: {self.judge_model} | Concurrency: {self.judge_workers} workers | Pacing: {pacing}s")
        print(f"==================================================")

        tasks = []
        for t in targets:
            doc_id = t["doc_id"]
            dom_path = self.normalized_dir / f"{doc_id}.parsed.v1.docJSON"
            if dom_path.exists():
                tasks.append({
                    "doc_id": doc_id,
                    "pdf_path": t["pdf_path"],
                    "dom_path": str(dom_path),
                    "out_verdict_path": str(self.logs_dir / f"{doc_id}.verdict.json"),
                    "pacing": pacing,
                    "model": self.judge_model,
                    "resume": resume,
                })

        judgments = []
        pass_count = 0
        pwi_count = 0
        fail_count = 0
        completed = 0
        t0 = time.perf_counter()

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.judge_workers) as executor:
            future_to_doc = {executor.submit(_judge_task, task): task for task in tasks}
            for future in concurrent.futures.as_completed(future_to_doc):
                task = future_to_doc[future]
                completed += 1
                try:
                    res = future.result()
                    j_data = res.get("data", {})
                    judgments.append(j_data)
                    cached_tag = " (cached)" if res.get("cached") else ""

                    v_status = j_data.get("verdict_status") or (
                        j_data.get("verdict") if isinstance(j_data.get("verdict"), str) else "PASS"
                    )
                    metrics = j_data.get("metrics", {})
                    t_score = metrics.get("tables", 0.0)
                    f_score = metrics.get("fidelity", 0.0)

                    if v_status == "PASS":
                        pass_count += 1
                    elif v_status == "PASS_WITH_ISSUES":
                        pwi_count += 1
                    else:
                        fail_count += 1

                    print(f"[{completed:3d}/{len(tasks)}] {task['doc_id']}{cached_tag} -> {v_status} (Tables: {t_score:.2f}, Fidelity: {f_score:.2f})", flush=True)
                except Exception as exc:
                    fail_count += 1
                    print(f"[{completed:3d}/{len(tasks)}] {task['doc_id']} -> ERROR ({exc})", flush=True)

        wall_time_s = time.perf_counter() - t0
        print(f"\n--- Phase 2 Complete ---")
        print(f"Judged {len(judgments)} documents in {wall_time_s:.1f}s")
        print(f"Verdicts: PASS={pass_count} ({pass_count/max(1, len(judgments))*100:.1f}%), PASS_WITH_ISSUES={pwi_count} ({pwi_count/max(1, len(judgments))*100:.1f}%), FAIL={fail_count} ({fail_count/max(1, len(judgments))*100:.1f}%)")
        return judgments

    def generate_consolidated_report(
        self,
        extraction_results: list[dict[str, Any]],
        judgment_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Calculates final metrics and compares against previous baseline run."""
        total_docs = len(extraction_results)
        total_pages = sum(r.get("page_count", 0) for r in extraction_results)
        total_tables = sum(r.get("tables_extracted", 0) for r in extraction_results)
        total_wall_ms = sum(r.get("telemetry", {}).get("total_wall_ms", 0.0) for r in extraction_results)
        peak_vram = max((r.get("telemetry", {}).get("peak_vram_reserved_mb", 0.0) for r in extraction_results), default=0.0)
        peak_ram = max((r.get("telemetry", {}).get("peak_ram_mb", 0.0) for r in extraction_results), default=0.0)
        failures = sum(1 for r in extraction_results if r.get("status") == "failed")

        metric_sums = {
            "completeness": 0.0,
            "fidelity": 0.0,
            "structure": 0.0,
            "tables": 0.0,
            "references": 0.0,
            "scans_ocr": 0.0,
        }
        valid_judgments = 0
        verdict_counts = {"PASS": 0, "PASS_WITH_ISSUES": 0, "FAIL": 0}

        prev_metric_sums = {k: 0.0 for k in metric_sums}
        prev_valid = 0
        prev_verdicts = {"PASS": 0, "PASS_WITH_ISSUES": 0, "FAIL": 0}

        for j in judgment_results:
            if not j or "metrics" not in j:
                continue
            m = j["metrics"]
            for k in metric_sums:
                metric_sums[k] += m.get(k, 0.0)
            valid_judgments += 1

            v = j.get("verdict_status") or (j.get("verdict") if isinstance(j.get("verdict"), str) else "PASS")
            verdict_counts[v] = verdict_counts.get(v, 0) + 1

            doc_id = j.get("doc_id") or j.get("document_id")
            if doc_id:
                prev_file = JUDGMENTS_DIR / f"{doc_id}.json"
                if prev_file.exists():
                    try:
                        prev_data = json.loads(prev_file.read_text(encoding="utf-8"))
                        if "verdict" in prev_data and isinstance(prev_data["verdict"], dict):
                            pv_metrics = prev_data["verdict"].get("metrics", {})
                            for k in prev_metric_sums:
                                prev_metric_sums[k] += pv_metrics.get(k, 0.0)
                            pv_status = prev_data["verdict"].get("verdict", "PASS")
                            prev_verdicts[pv_status] = prev_verdicts.get(pv_status, 0) + 1
                            prev_valid += 1
                    except Exception:
                        pass

        avg_metrics = {k: round(v / max(1, valid_judgments), 4) for k, v in metric_sums.items()}
        prev_avg_metrics = {k: round(v / max(1, prev_valid), 4) for k, v in prev_metric_sums.items()} if prev_valid > 0 else {}

        summary = {
            "strategy_id": self.strategy_id,
            "corpus": "Corpus 945 (PMC Open Access)",
            "total_documents": total_docs,
            "total_pages": total_pages,
            "total_tables": total_tables,
            "total_extraction_wall_ms": total_wall_ms,
            "mean_page_ms": round(total_wall_ms / max(1, total_pages), 2) if total_pages > 0 else 0.0,
            "effective_pages_per_sec": round((total_pages / (total_wall_ms / 1000.0)), 3) if total_wall_ms > 0 else 0.0,
            "peak_vram_mb": peak_vram,
            "peak_ram_mb": peak_ram,
            "failures": failures,
            "judged_documents": valid_judgments,
            "quality_metrics": avg_metrics,
            "verdict_distribution": verdict_counts,
            "baseline_comparison": {
                "matched_documents": prev_valid,
                "baseline_quality_metrics": prev_avg_metrics,
                "baseline_verdict_distribution": prev_verdicts,
                "metric_deltas": {
                    k: round(avg_metrics.get(k, 0.0) - prev_avg_metrics.get(k, 0.0), 4)
                    for k in avg_metrics
                    if k in prev_avg_metrics
                },
            },
        }

        # Save JSON scorecard
        out_json = EVAL_DIR / "corpus_945_p003_results.json"
        out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\n[REPORT] Saved summary JSON to {out_json}")

        # Save Markdown Report
        md_lines = [
            f"# Corpus 945 Benchmark Report: P003 Hybrid vs Historical Baseline",
            f"",
            f"**Date:** 2026-09-17  ",
            f"**Strategy Tested:** `{self.strategy_id}` (PyMuPDF Hybrid Table Extraction + RapidOCR GPU + OCR Occlusion Suppression)  ",
            f"**Dataset:** Corpus 945 ({total_docs} PMC Open Access Documents, {total_pages} Pages)  ",
            f"**LLM Judge:** Google Gemini 3.5 Flash Lite ({valid_judgments} Documents Judged)  ",
            f"",
            f"## 1. Executive Summary & Paired Scorecard",
            f"",
            f"| Metric / Dimension | P003 Engine | Historical Baseline | Absolute Delta | Relative Gain |",
            f"| :--- | :--- | :--- | :--- | :--- |",
        ]

        for k in ["tables", "structure", "completeness", "fidelity", "references", "scans_ocr"]:
            p_val = avg_metrics.get(k, 0.0) * 100
            b_val = prev_avg_metrics.get(k, 0.0) * 100 if prev_avg_metrics else 0.0
            delta = p_val - b_val
            rel = (delta / b_val * 100) if b_val > 0 else 0.0
            md_lines.append(f"| **{k.replace('_', ' ').title()}** | **{p_val:.2f}%** | {b_val:.2f}% | **{delta:+.2f}%** | **{rel:+.1f}%** |")

        md_lines.extend([
            f"",
            f"## 2. Verdict Distribution Comparison",
            f"",
            f"| Verdict Status | P003 (N={valid_judgments}) | Baseline (N={prev_valid}) |",
            f"| :--- | :--- | :--- |",
            f"| **PASS (Clean)** | **{verdict_counts.get('PASS', 0)} ({verdict_counts.get('PASS',0)/max(1,valid_judgments)*100:.1f}%)** | {prev_verdicts.get('PASS', 0)} ({prev_verdicts.get('PASS',0)/max(1,prev_valid)*100:.1f}%) |",
            f"| **PASS_WITH_ISSUES** | **{verdict_counts.get('PASS_WITH_ISSUES', 0)} ({verdict_counts.get('PASS_WITH_ISSUES',0)/max(1,valid_judgments)*100:.1f}%)** | {prev_verdicts.get('PASS_WITH_ISSUES', 0)} ({prev_verdicts.get('PASS_WITH_ISSUES',0)/max(1,prev_valid)*100:.1f}%) |",
            f"| **FAIL** | **{verdict_counts.get('FAIL', 0)} ({verdict_counts.get('FAIL',0)/max(1,valid_judgments)*100:.1f}%)** | {prev_verdicts.get('FAIL', 0)} ({prev_verdicts.get('FAIL',0)/max(1,prev_valid)*100:.1f}%) |",
            f"",
            f"## 3. Hardware & Throughput Profile",
            f"",
            f"- **Total Documents Processed:** {total_docs}",
            f"- **Total Pages Parsed:** {total_pages}",
            f"- **Total Tables Extracted:** {total_tables}",
            f"- **Failures / Unparsed:** {failures}",
            f"- **Mean Page Latency:** {summary['mean_page_ms']} ms/page",
            f"- **Effective Throughput:** {summary['effective_pages_per_sec']} pages/sec",
            f"- **Peak Host RAM:** {peak_ram:.1f} MB RSS",
            f"- **Peak GPU VRAM:** {peak_vram:.1f} MB",
        ])

        out_md = WORKSPACE_ROOT / "docs" / "corpus-945-p003-vs-baseline-benchmark.md"
        out_md.write_text("\n".join(md_lines), encoding="utf-8")
        print(f"[REPORT] Saved summary Markdown report to {out_md}")

        return summary


def run_eval(
    limit: int = 0,
    judge_workers: int = 4,
    strategy_id: str = "P003",
    pacing: float = 1.0,
    resume: bool = True,
):
    evaluator = Corpus945BatchEvaluator(
        strategy_id=strategy_id,
        judge_workers=judge_workers,
    )
    targets = evaluator.discover_target_documents(limit=limit)
    print(f"[TARGETS] Discovered {len(targets)} documents from Corpus 945.")

    ext_results = evaluator.run_batch_extraction(targets, resume=resume)
    judge_results = evaluator.run_batch_judgment(targets, resume=resume, pacing=pacing)
    evaluator.generate_consolidated_report(ext_results, judge_results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate P003 on Corpus 945")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of documents (0=all 945)")
    parser.add_argument("--judge-workers", type=int, default=4, help="LLM judge concurrent workers")
    parser.add_argument("--strategy", type=str, default="P003", help="Strategy ID (default: P003)")
    parser.add_argument("--pacing", type=float, default=1.0, help="Judge request pacing in seconds")
    parser.add_argument("--no-resume", action="store_true", help="Force re-extraction")
    args = parser.parse_args()

    run_eval(
        limit=args.limit,
        judge_workers=args.judge_workers,
        strategy_id=args.strategy,
        pacing=args.pacing,
        resume=not args.no_resume,
    )
