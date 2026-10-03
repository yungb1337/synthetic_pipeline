"""Aggregates and formats live benchmarking metrics across Curated Hard and Curated Easy runs."""

from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]


def collect_run_metrics(run_dir: Path) -> dict[str, Any]:
    raw_dir = run_dir / "raw_output"
    files = sorted(glob.glob(str(raw_dir / "*.runtime.json")))
    if not files:
        # Check batch_summary.json
        summary_p = run_dir / "batch_summary.json"
        if summary_p.exists():
            try:
                return json.loads(summary_p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    total_docs = len(files)
    total_pages = 0
    total_blocks = 0
    total_tables = 0
    total_references = 0
    total_elapsed_ms = 0.0
    page_routes = {"rust_native": 0, "cuda_ocr": 0, "docling_heavy": 0}
    doc_routes = {"rust_native": 0, "cuda_ocr": 0, "docling_heavy": 0}
    max_ram_rss_gb = 0.0

    for f_path in files:
        try:
            d = json.loads(Path(f_path).read_text(encoding="utf-8"))
            p_cnt = d.get("page_count", 0)
            total_pages += p_cnt
            total_blocks += d.get("num_blocks", 0)
            total_tables += d.get("num_tables", 0)
            total_references += d.get("num_references", 0)
            total_elapsed_ms += d.get("elapsed_ms", 0.0)

            d_route = d.get("route", "rust_native")
            doc_routes[d_route] = doc_routes.get(d_route, 0) + 1

            for r_k, r_v in d.get("route_breakdown", {}).items():
                page_routes[r_k] = page_routes.get(r_k, 0) + r_v

            rss = d.get("hardware", {}).get("ram_rss_gb", 0.0)
            max_ram_rss_gb = max(max_ram_rss_gb, rss)
        except Exception:
            pass

    wall_s = total_elapsed_ms / 1000.0
    pages_per_sec = (total_pages / wall_s) if wall_s > 0 else 0.0

    return {
        "total_documents": total_docs,
        "total_pages": total_pages,
        "total_blocks": total_blocks,
        "total_tables": total_tables,
        "total_references": total_references,
        "total_wall_seconds": round(wall_s, 2),
        "pages_per_sec": round(pages_per_sec, 2),
        "peak_ram_rss_gb": round(max_ram_rss_gb, 3),
        "document_routes": doc_routes,
        "page_routes": page_routes,
    }


def print_comprehensive_metrics():
    easy_dir = ROOT_DIR / "artifacts" / "pdf_inspector_eval" / "curated_easy"
    hard_dir = ROOT_DIR / "artifacts" / "pdf_inspector_eval" / "curated_hard"

    easy_m = collect_run_metrics(easy_dir)
    hard_m = collect_run_metrics(hard_dir)

    print("=" * 80)
    print("CALIBRATED HYBRID PIPELINE EVALUATION: COMPREHENSIVE METRICS")
    print("=" * 80)

    print("\n1. CURATED EASY CORPUS (Clean Text Baseline)")
    print(f"  - Total Documents Processed : {easy_m.get('total_documents', 0)}")
    print(f"  - Total Pages Parsed        : {easy_m.get('total_pages', 0)}")
    print(f"  - Total Blocks Extracted    : {easy_m.get('total_blocks', 0)}")
    print(f"  - Total Tables Extracted    : {easy_m.get('total_tables', 0)}")
    print(f"  - Total References Extracted: {easy_m.get('total_references', 0)}")
    print(f"  - Throughput (Pages/Sec)    : {easy_m.get('pages_per_sec', 0.0)} p/s")
    print(f"  - Peak Memory (RAM RSS)     : {easy_m.get('peak_ram_rss_gb', 0.0)} GB")
    print(f"  - Page Route Breakdown      : {easy_m.get('page_routes', {})}")

    print("\n2. CURATED HARD CORPUS (Table-Heavy Calibrated Single-Page Docling)")
    print(f"  - Total Documents Processed : {hard_m.get('total_documents', 0)}")
    print(f"  - Total Pages Parsed        : {hard_m.get('total_pages', 0)}")
    print(f"  - Total Blocks Extracted    : {hard_m.get('total_blocks', 0)}")
    print(f"  - Total Tables Extracted    : {hard_m.get('total_tables', 0)}")
    print(f"  - Total References Extracted: {hard_m.get('total_references', 0)}")
    print(
        f"  - Cumulative Elapsed Time   : {hard_m.get('total_wall_seconds', 0.0):.1f} s ({hard_m.get('total_wall_seconds', 0.0) / 60.0:.2f} min)"
    )
    print(f"  - Average Throughput        : {hard_m.get('pages_per_sec', 0.0)} p/s")
    print(f"  - Peak Memory (RAM RSS)     : {hard_m.get('peak_ram_rss_gb', 0.0)} GB")
    print(f"  - Page Route Breakdown      : {hard_m.get('page_routes', {})}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    print_comprehensive_metrics()
