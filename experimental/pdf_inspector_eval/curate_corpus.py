"""Corpus Curation Script for pdf-inspector Hybrid Calibration Evaluation.
Separates Corpus B (1,000 docs) and Corpus 945 (945 docs) into:
- Curated Hard: documents containing tabular layouts / complex multi-column tables
- Curated Easy: sample of clean, text-based documents without tables
Preserves source corpus provenance in distinct subdirectories.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pdf_inspector


def link_file(src: Path, dest: Path) -> None:
    if dest.exists():
        return
    try:
        os.link(str(src), str(dest))
    except Exception:
        try:
            os.symlink(str(src), str(dest))
        except Exception:
            shutil.copy2(src, dest)


def curate_corpora(
    easy_sample_per_corpus: int = 50,
    output_base: Path | None = None,
) -> dict[str, Any]:
    if output_base is None:
        output_base = ROOT_DIR / "artifacts"

    corpus_b_dir = (
        ROOT_DIR
        / "checkpoints"
        / "run"
        / "run-2026-09-14-eval-1000"
        / "sources"
        / "pdf"
    )
    corpus_945_dir = (
        ROOT_DIR
        / "checkpoints"
        / "run"
        / "run-2026-09-04-parser-reliability"
        / "sources"
        / "pdf"
    )

    hard_b_dir = output_base / "curated_hard" / "corpus_b"
    hard_945_dir = output_base / "curated_hard" / "corpus_945"
    easy_b_dir = output_base / "curated_easy" / "corpus_b"
    easy_945_dir = output_base / "curated_easy" / "corpus_945"

    for d in (hard_b_dir, hard_945_dir, easy_b_dir, easy_945_dir):
        d.mkdir(parents=True, exist_ok=True)

    sources = [
        ("corpus_b", corpus_b_dir, hard_b_dir, easy_b_dir),
        ("corpus_945", corpus_945_dir, hard_945_dir, easy_945_dir),
    ]

    manifest: list[dict[str, Any]] = []
    stats = {
        "corpus_b": {"total": 0, "hard": 0, "easy": 0, "total_table_pages": 0},
        "corpus_945": {"total": 0, "hard": 0, "easy": 0, "total_table_pages": 0},
        "curated_hard_total": 0,
        "curated_easy_total": 0,
    }

    t0 = time.perf_counter()
    print("=" * 70)
    print("Curating Hard vs Easy Documents Across Corpus B & Corpus 945")
    print(f"Output Base: {output_base}")
    print("=" * 70)

    for corpus_name, src_dir, hard_dest, easy_dest in sources:
        pdf_files = sorted(glob.glob(str(src_dir / "*.pdf")))
        print(f"\nScanning {corpus_name} ({len(pdf_files)} PDFs)...")
        stats[corpus_name]["total"] = len(pdf_files)

        easy_candidates: list[dict[str, Any]] = []

        for idx, pdf_p in enumerate(pdf_files, 1):
            p = Path(pdf_p)
            doc_id = p.stem
            try:
                proc_res = pdf_inspector.process_pdf(str(p))
                tbl_pages = list(getattr(proc_res, "pages_with_tables", []) or [])
                col_pages = list(getattr(proc_res, "pages_with_columns", []) or [])
                ocr_pages = list(getattr(proc_res, "pages_needing_ocr", []) or [])
                pg_count = int(getattr(proc_res, "page_count", 1) or 1)
                conf = float(getattr(proc_res, "confidence", 1.0))

                is_hard = (
                    len(tbl_pages) > 0
                    or len(ocr_pages) > 0
                    or (len(col_pages) > 0 and conf < 0.80)
                )

                doc_entry = {
                    "doc_id": doc_id,
                    "source_corpus": corpus_name,
                    "source_path": str(p),
                    "page_count": pg_count,
                    "table_pages": tbl_pages,
                    "column_pages": col_pages,
                    "ocr_pages": ocr_pages,
                    "confidence": conf,
                    "classification": "hard" if is_hard else "easy",
                }

                if is_hard:
                    dest_file = hard_dest / p.name
                    link_file(p, dest_file)
                    doc_entry["curated_path"] = str(dest_file)
                    manifest.append(doc_entry)
                    stats[corpus_name]["hard"] += 1
                    stats[corpus_name]["total_table_pages"] += len(tbl_pages)
                else:
                    easy_candidates.append(doc_entry)

                if idx % 200 == 0 or idx == len(pdf_files):
                    print(
                        f"[{idx:4d}/{len(pdf_files):4d}] Hard so far: {stats[corpus_name]['hard']} | Easy candidates: {len(easy_candidates)}"
                    )

            except Exception as exc:
                print(f"[ERROR] {doc_id}: {exc}")

        # Sample easy documents for this corpus
        sampled_easy = easy_candidates[:easy_sample_per_corpus]
        for e_entry in sampled_easy:
            src_p = Path(e_entry["source_path"])
            dest_file = easy_dest / src_p.name
            link_file(src_p, dest_file)
            e_entry["curated_path"] = str(dest_file)
            manifest.append(e_entry)
            stats[corpus_name]["easy"] += 1

        print(
            f"-> {corpus_name}: Curated {stats[corpus_name]['hard']} HARD docs and {stats[corpus_name]['easy']} EASY docs."
        )

    stats["curated_hard_total"] = (
        stats["corpus_b"]["hard"] + stats["corpus_945"]["hard"]
    )
    stats["curated_easy_total"] = (
        stats["corpus_b"]["easy"] + stats["corpus_945"]["easy"]
    )
    stats["elapsed_seconds"] = round(time.perf_counter() - t0, 2)

    manifest_path = output_base / "curated_manifest.json"
    manifest_path.write_text(
        json.dumps({"stats": stats, "manifest": manifest}, indent=2), encoding="utf-8"
    )

    print("\n" + "=" * 70)
    print("CURATION SUMMARY")
    print(
        f"Curated Hard Total: {stats['curated_hard_total']} documents (Corpus B: {stats['corpus_b']['hard']}, Corpus 945: {stats['corpus_945']['hard']})"
    )
    print(
        f"Curated Easy Total: {stats['curated_easy_total']} documents (Corpus B: {stats['corpus_b']['easy']}, Corpus 945: {stats['corpus_945']['easy']})"
    )
    print(f"Total Manifest Entries: {len(manifest)}")
    print(f"Manifest written to: {manifest_path}")
    print("=" * 70)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Curate Hard and Easy PDF Corpora")
    parser.add_argument(
        "--easy-sample",
        type=int,
        default=50,
        help="Number of easy PDFs to sample per corpus",
    )
    args = parser.parse_args()
    curate_corpora(easy_sample_per_corpus=args.easy_sample)


if __name__ == "__main__":
    main()
