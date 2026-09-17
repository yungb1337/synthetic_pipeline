"""Batch benchmark runner managing staged evaluation across document corpora.
"""
from __future__ import annotations

import gc
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

import fitz

from .artifacts import BenchmarkArtifactManager
from .config import BenchmarkConfig, PERMUTATIONS, PermutationSpec
from .converter import TableBenchmarkDOMConverter
from .judge_evaluator import TableBenchmarkJudgeEvaluator
from .profiler import HardwareProfile, HardwareProfiler
from .strategies import ExecutionStrategy


class BenchmarkRunner:
    """Orchestrates multi-stage benchmarking across layout/table permutations."""

    def __init__(self, config: Optional[BenchmarkConfig] = None):
        self.config = config or BenchmarkConfig()
        self.artifacts = BenchmarkArtifactManager(self.config.artifacts_dir, self.config.evaluation_dir)
        self.profiler = HardwareProfiler(safety_threshold_mb=self.config.vram_safety_ceiling_mb)
        self.converter = TableBenchmarkDOMConverter()
        self.judge = TableBenchmarkJudgeEvaluator(
            model=self.config.judge_model,
            max_chars=self.config.judge_max_chars,
            pacing_seconds=self.config.judge_pacing_seconds,
        )

    def run_permutation_on_doc(
        self,
        strategy: ExecutionStrategy,
        pdf_path: Path,
        doc_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Runs a strategy on a single PDF document with strict telemetry profiling."""
        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        source_bytes = pdf_path.read_bytes()
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        d_id = doc_id or pdf_path.stem

        t0_wall = time.perf_counter()
        prof = self.profiler.start_profile()

        page_results = []
        doc_tables_count = 0
        status = "success"
        error_msg = None

        try:
            with fitz.open(pdf_path) as doc:
                page_count = doc.page_count
                for p_idx in range(page_count):
                    page = doc[p_idx]
                    p_res = strategy.process_page(
                        page,
                        p_idx,
                        str(pdf_path),
                        render_dpi=self.config.render_dpi,
                    )
                    page_results.append(p_res)
                    doc_tables_count += len(p_res.get("tables", []))

            # Convert to Canonical DOM
            t_dom0 = time.perf_counter()
            canonical_dom = self.converter.build_canonical_dom(
                document_id=d_id,
                source_sha256=source_sha,
                page_results=page_results,
                strategy_id=strategy.spec.id,
            )
            dom_ms = (time.perf_counter() - t_dom0) * 1000.0

            # Save canonical DOM
            dom_path = self.artifacts.save_document_dom(strategy.spec.id, d_id, canonical_dom)

        except Exception as exc:
            status = "failed"
            error_msg = str(exc)
            page_count = 0
            dom_path = None
            dom_ms = 0.0

        total_wall_ms = (time.perf_counter() - t0_wall) * 1000.0
        prof.dom_conversion_ms = dom_ms
        prof = self.profiler.finalize_profile(prof, total_wall_ms)

        runtime_data = {
            "strategy_id": strategy.spec.id,
            "document_id": d_id,
            "source_sha256": source_sha,
            "page_count": page_count,
            "tables_extracted": doc_tables_count,
            "status": status,
            "error": error_msg,
            "telemetry": prof.to_dict(),
            "dom_path": str(dom_path) if dom_path else None,
        }

        self.artifacts.save_runtime_telemetry(strategy.spec.id, d_id, runtime_data)
        if status == "failed":
            self.artifacts.record_failure(strategy.spec.id, d_id, error_msg or "unknown error")

        return runtime_data

    def run_stage(
        self,
        stage: str,  # "smoke", "performance", "judge", "all"
        strategy_ids: list[str],
        pdf_paths: list[Path],
        force_extract: bool = False,
    ) -> dict[str, Any]:
        """Runs the benchmark stage across designated permutations and documents."""
        stage_summary: dict[str, Any] = {}

        for s_id in strategy_ids:
            if s_id not in PERMUTATIONS:
                print(f"[WARN] Skipping unknown permutation ID: {s_id}")
                continue

            spec = PERMUTATIONS[s_id]
            print(f"\n==================================================")
            print(f"Executing Strategy {spec.id} ({spec.name}) — Stage: {stage.upper()}")
            print(f"Description: {spec.description}")
            print(f"==================================================")

            s_dir = self.artifacts.get_strategy_dir(spec.id)
            doc_summaries = []

            total_pages = 0
            total_time_ms = 0.0
            peak_vram = 0.0
            peak_ram = 0.0
            tables_total = 0
            failures = 0

            # If stage is 'judge' and docJSONs already exist and not force_extract, skip extraction
            skip_extraction = (
                stage == "judge"
                and not force_extract
                and all((s_dir / "normalized_output" / f"{p.stem}.parsed.v1.docJSON").exists() for p in pdf_paths)
            )

            if not skip_extraction:
                strategy = ExecutionStrategy(spec)
                for pdf_p in pdf_paths:
                    print(f"  -> Processing document: {pdf_p.name} ...", end=" ", flush=True)
                    res = self.run_permutation_on_doc(strategy, pdf_p)
                    doc_summaries.append(res)

                    p_cnt = res.get("page_count", 0)
                    total_pages += p_cnt
                    t_ms = res["telemetry"]["total_wall_ms"]
                    total_time_ms += t_ms
                    tables_total += res.get("tables_extracted", 0)

                    vram = res["telemetry"]["peak_vram_reserved_mb"]
                    ram = res["telemetry"]["peak_ram_mb"]
                    peak_vram = max(peak_vram, vram)
                    peak_ram = max(peak_ram, ram)

                    if res["status"] == "failed":
                        failures += 1
                        print(f"FAILED ({res.get('error', '')})")
                    else:
                        pps = (p_cnt / (t_ms / 1000.0)) if t_ms > 0 else 0.0
                        print(f"OK ({p_cnt} pgs, {t_ms:.1f}ms, {pps:.2f} pgs/s, {res['tables_extracted']} tbls, {vram:.1f}MB VRAM)")

                # Aggregate strategy metrics
                pages_per_sec = (total_pages / (total_time_ms / 1000.0)) if total_time_ms > 0 else 0.0
                ms_per_page = (total_time_ms / total_pages) if total_pages > 0 else 0.0

                strat_summary = {
                    "strategy_id": spec.id,
                    "strategy_name": spec.name,
                    "description": spec.description,
                    "total_documents": len(pdf_paths),
                    "total_pages": total_pages,
                    "total_tables_extracted": tables_total,
                    "total_time_ms": total_time_ms,
                    "pages_per_sec": round(pages_per_sec, 3),
                    "ms_per_page": round(ms_per_page, 2),
                    "peak_vram_mb": round(peak_vram, 2),
                    "peak_ram_mb": round(peak_ram, 2),
                    "failures": failures,
                    "vram_safe": peak_vram <= self.config.vram_safety_ceiling_mb,
                }

                self.artifacts.update_experiment_registry(spec.id, strat_summary)
            else:
                print(f"  -> Found existing normalized docJSONs for all {len(pdf_paths)} documents. Skipping re-extraction.")
                experiments = self.artifacts.load_experiment_registry()
                strat_summary = experiments.get(spec.id, {
                    "strategy_id": spec.id,
                    "strategy_name": spec.name,
                    "description": spec.description,
                })

            stage_summary[spec.id] = strat_summary

            # Optional Judge evaluation if stage is judge
            if stage in ("judge", "all") and failures == 0:
                print(f"  -> Running LLM Judge evaluation for {spec.id} ...")
                judge_metrics_accum = {
                    "completeness": 0.0,
                    "fidelity": 0.0,
                    "structure": 0.0,
                    "tables": 0.0,
                    "references": 0.0,
                    "scans_ocr": 0.0,
                }
                judged_count = 0

                s_dir = self.artifacts.get_strategy_dir(spec.id)
                for pdf_p in pdf_paths:
                    dom_file = s_dir / "normalized_output" / f"{pdf_p.stem}.parsed.v1.docJSON"
                    verdict_file = s_dir / "logs" / f"{pdf_p.stem}.verdict.json"

                    if dom_file.exists():
                        print(f"     Judging {pdf_p.name} ...", end=" ", flush=True)
                        j_res = self.judge.evaluate_doc(pdf_p, dom_file, verdict_file)
                        if j_res and "metrics" in j_res:
                            m = j_res["metrics"]
                            for k in judge_metrics_accum:
                                judge_metrics_accum[k] += m.get(k, 0.0)
                            judged_count += 1
                            v_label = j_res.get("verdict_status") or (j_res.get("verdict") if isinstance(j_res.get("verdict"), str) else "PASS")
                            print(f"Verdict: {v_label} (Tables: {m.get('tables', 0.0):.2f}, Fidelity: {m.get('fidelity', 0.0):.2f})")
                        else:
                            print(f"Judge error: {j_res.get('error', 'unknown') if j_res else 'no result'}")

                if judged_count > 0:
                    avg_metrics = {k: round(v / judged_count, 3) for k, v in judge_metrics_accum.items()}
                    strat_summary["judge_metrics"] = avg_metrics
                    self.artifacts.save_judge_results(spec.id, avg_metrics)
                    print(f"  -> Judge Averages: {avg_metrics}")

            # Force memory cleanup between strategies
            gc.collect()
            if self.profiler.cuda_available:
                import torch
                torch.cuda.empty_cache()

        return stage_summary
