"""Batch Evaluation Orchestrator for pdf-inspector hybrid extraction.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import fitz
import psutil

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from .dom_converter import PDFInspectorDOMConverter
from .inspector_adapter import PDFInspectorAdapter
from .smart_router import PDFInspectorSmartRouter


class PDFInspectorBatchEvaluator:
    """Orchestrates batch extraction and performance telemetry collection."""

    def __init__(
        self,
        output_dir: str | Path = "artifacts/pdf_inspector_eval/corpus_b",
    ):
        self.output_dir = Path(output_dir).resolve()
        self.normalized_dir = self.output_dir / "normalized_output"
        self.raw_dir = self.output_dir / "raw_output"

        self.normalized_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.adapter = PDFInspectorAdapter()
        self.converter = PDFInspectorDOMConverter()
        self.router = PDFInspectorSmartRouter()
        self.process = psutil.Process()

    def discover_corpus_targets(self, corpus_type: str = "corpus_b") -> list[dict[str, Any]]:
        """Discovers PDF target files from Corpus B (1000-doc), Corpus 945, or Curated folders."""
        if corpus_type == "corpus_b":
            pdf_dir = ROOT_DIR / "checkpoints" / "run" / "run-2026-09-14-eval-1000" / "sources" / "pdf"
        elif corpus_type == "corpus_945":
            pdf_dir = ROOT_DIR / "checkpoints" / "run" / "run-2026-09-04-parser-reliability" / "sources" / "pdf"
        elif corpus_type in ("curated_hard", "curated_easy"):
            pdf_dir = ROOT_DIR / "artifacts" / corpus_type
        else:
            pdf_dir = Path(corpus_type).resolve()

        all_pdfs = sorted(glob.glob(str(pdf_dir / "**" / "*.pdf"), recursive=True))
        targets = []
        for p in all_pdfs:
            p_obj = Path(p)
            targets.append({
                "doc_id": p_obj.stem,
                "pdf_path": str(p_obj),
            })
        return targets

    def run_batch(
        self,
        targets: list[dict[str, Any]],
        limit: int | None = None,
        resume: bool = True,
    ) -> list[dict[str, Any]]:
        if limit:
            targets = targets[:limit]

        total_docs = len(targets)
        print("=" * 60)
        print(f"Starting pdf-inspector Batch Evaluation ({total_docs} Documents)")
        print(f"Output Directory: {self.output_dir}")
        print("=" * 60)

        results = []
        start_time = time.perf_counter()
        total_pages_parsed = 0

        route_counts = {"rust_native": 0, "cuda_ocr": 0, "docling_heavy": 0}

        page_route_counts = {"rust_native": 0, "cuda_ocr": 0, "docling_heavy": 0}

        for idx, target in enumerate(targets, 1):
            doc_id = target["doc_id"]
            pdf_path = Path(target["pdf_path"])
            out_dom_path = self.normalized_dir / f"{doc_id}.parsed.v1.docJSON"
            out_telemetry_path = self.raw_dir / f"{doc_id}.runtime.json"

            if resume and out_dom_path.exists() and out_telemetry_path.exists():
                try:
                    data = json.loads(out_telemetry_path.read_text(encoding="utf-8"))
                    results.append(data)
                    total_pages_parsed += data.get("page_count", 0)
                    r_band = data.get("route", "rust_native")
                    route_counts[r_band] = route_counts.get(r_band, 0) + 1
                    for r_k, r_v in data.get("route_breakdown", {}).items():
                        page_route_counts[r_k] = page_route_counts.get(r_k, 0) + r_v
                    print(f"[{idx:4d}/{total_docs:4d}] {doc_id} ... CACHED ({data.get('page_count', 0)} pgs, {data.get('elapsed_ms', 0.0):.1f} ms)")
                    continue
                except Exception:
                    pass

            doc_t0 = time.perf_counter()
            try:
                # 1. Route document
                decision = self.router.route_document(pdf_path)
                route_counts[decision.route] = route_counts.get(decision.route, 0) + 1

                # 2. Extract layout & markdown
                ext_result = self.adapter.inspect_and_extract(pdf_path, doc_id=doc_id)
                for r_k, r_v in ext_result.route_breakdown.items():
                    page_route_counts[r_k] = page_route_counts.get(r_k, 0) + r_v

                # 3. Build canonical DOM
                dom = self.converter.build_canonical_dom(ext_result)

                doc_elapsed = (time.perf_counter() - doc_t0) * 1000.0
                page_count = len(dom.pages)
                total_pages_parsed += page_count

                # 4. Save normalized DOM
                dom_dict = dom.model_dump()
                out_dom_path.write_text(json.dumps(dom_dict, indent=2), encoding="utf-8")

                # 5. Save telemetry
                mem_info = self.process.memory_info()
                rss_gb = mem_info.rss / (1024 ** 3)
                telemetry = {
                    "doc_id": doc_id,
                    "pdf_path": str(pdf_path),
                    "status": "ok",
                    "route": decision.route,
                    "route_breakdown": ext_result.route_breakdown,
                    "confidence": decision.confidence,
                    "has_encoding_issues": decision.has_encoding_issues,
                    "is_complex_layout": decision.is_complex_layout,
                    "pdf_type": decision.pdf_type,
                    "page_count": page_count,
                    "num_blocks": dom.num_blocks(),
                    "num_tables": dom.num_tables(),
                    "num_references": len(dom.references),
                    "elapsed_ms": doc_elapsed,
                    "pages_per_sec": (page_count / (doc_elapsed / 1000.0)) if doc_elapsed > 0 else 0.0,
                    "hardware": {
                        "ram_rss_gb": round(rss_gb, 3),
                        "ram_percent": round(psutil.virtual_memory().percent, 1),
                        "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
                    },
                }
                out_telemetry_path.write_text(json.dumps(telemetry, indent=2), encoding="utf-8")
                results.append(telemetry)

                print(f"[{idx:4d}/{total_docs:4d}] {doc_id} ... OK ({page_count} pgs, {doc_elapsed:.1f} ms, {telemetry['pages_per_sec']:.1f} p/s, Route: {decision.route})")

            except Exception as exc:
                doc_elapsed = (time.perf_counter() - doc_t0) * 1000.0
                telemetry = {
                    "doc_id": doc_id,
                    "pdf_path": str(pdf_path),
                    "status": "failed",
                    "error": str(exc),
                    "elapsed_ms": doc_elapsed,
                }
                out_telemetry_path.write_text(json.dumps(telemetry, indent=2), encoding="utf-8")
                results.append(telemetry)
                print(f"[{idx:4d}/{total_docs:4d}] {doc_id} ... FAILED ({exc})")

        total_wall_s = time.perf_counter() - start_time
        success_count = sum(1 for r in results if r.get("status") == "ok")
        print("\n" + "=" * 60)
        print("pdf-inspector Batch Evaluation Summary")
        print(f"Total Documents: {total_docs} | Success: {success_count} | Failed: {total_docs - success_count}")
        print(f"Total Pages Parsed: {total_pages_parsed}")
        print(f"Total Wall Time: {total_wall_s:.2f} s ({total_wall_s / 60.0:.2f} min)")
        if total_wall_s > 0:
            print(f"Effective Throughput: {total_pages_parsed / total_wall_s:.2f} pages/sec ({total_docs / total_wall_s:.3f} docs/sec)")
        print(f"Document Route Breakdown: {route_counts}")
        print(f"Page Route Breakdown: {page_route_counts}")
        print("=" * 60)

        summary_data = {
            "total_documents": total_docs,
            "success_count": success_count,
            "failed_count": total_docs - success_count,
            "total_pages_parsed": total_pages_parsed,
            "total_wall_seconds": round(total_wall_s, 2),
            "effective_throughput_pages_per_sec": round(total_pages_parsed / total_wall_s, 2) if total_wall_s > 0 else 0.0,
            "document_routes": route_counts,
            "page_routes": page_route_counts,
        }
        (self.output_dir / "batch_summary.json").write_text(json.dumps(summary_data, indent=2), encoding="utf-8")

        return results


def main():
    parser = argparse.ArgumentParser(description="Run pdf-inspector batch evaluation")
    parser.add_argument("--corpus", default="corpus_b", help="Corpus to evaluate ('corpus_b', 'corpus_945', or directory)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of documents")
    parser.add_argument("--out-dir", default="artifacts/pdf_inspector_eval/corpus_b", help="Output artifact directory")
    parser.add_argument("--no-resume", action="store_true", help="Do not resume cached runs")
    args = parser.parse_args()

    evaluator = PDFInspectorBatchEvaluator(output_dir=args.out_dir)
    targets = evaluator.discover_corpus_targets(args.corpus)
    evaluator.run_batch(targets, limit=args.limit, resume=not args.no_resume)


if __name__ == "__main__":
    main()
