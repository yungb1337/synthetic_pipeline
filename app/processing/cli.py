"""CLI for the batch processing layer.

Usage (parse + normalize a whole corpus in parallel, incrementally):
    python -m app.processing.cli --in <corpus_dir> --store <out>
        [--concurrency N] [--manifest work/manifest.json] [--no-ocr] [--embed]

The manifest makes each run idempotent/incremental: files whose sha256 is
already in the manifest are skipped, so a 1M-document corpus resumes cheaply
and re-runs pick up only new/changed files.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

from ..parser.storage import FilesystemStore
from .config import ProcessingConfig
from .corpus import hash_paths, scan_dir
from .executor import BatchWorker, ParseNormalizePipeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synthetic Data Factory — batch processing (Parse+Normalize)")
    ap.add_argument("--in", dest="input", required=True, help="corpus directory")
    ap.add_argument("--out", dest="out", default="parser_out", help="store root")
    ap.add_argument("--concurrency", type=int, default=None)
    ap.add_argument("--native-concurrency", type=int, default=None,
                    help="override native (ThreadPool) pool size")
    ap.add_argument("--heavy-concurrency", type=int, default=None,
                    help="override Docling (ProcessPool) pool size (auto by default)")
    ap.add_argument("--manifest", default="work/manifest.json")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--embed", action="store_true", help="also run batched (dummy) embeddings over normalized blocks")
    args = ap.parse_args(argv)

    cfg = ProcessingConfig()
    if args.concurrency is not None:
        cfg = replace(cfg, concurrency=args.concurrency)
    if args.native_concurrency is not None:
        cfg = replace(cfg, native_concurrency=args.native_concurrency)
    if args.heavy_concurrency is not None:
        cfg = replace(cfg, heavy_concurrency=args.heavy_concurrency)
    if args.no_ocr:
        cfg = replace(cfg, ocr_warm=False)
    cfg = replace(cfg, manifest_path=args.manifest)

    store = FilesystemStore(args.out)
    pipeline = ParseNormalizePipeline(store, config=cfg)
    worker = BatchWorker(cfg, pipeline)

    print(f"scanning {args.input} ...")
    paths = scan_dir(args.input, cfg.exts)
    if not paths:
        print("no supported files found", file=sys.stderr)
        return 2

    print(f"hashing {len(paths)} files (parallel) ...")
    refs = hash_paths(paths, cfg.concurrency)
    report = worker.run(refs)
    _print_report(report)

    if args.embed:
        _embed_pass(report, store, cfg)

    _write_report(args.out, report)
    # Release the shared heavy pool (and any worker processes) exactly once,
    # at process exit — the pipeline is reused across runs within a process.
    ParseNormalizePipeline.close_scheduler()
    return 0


def _print_report(r) -> None:
    print("\n==== Batch report ====")
    print(f"  total       : {r.total}")
    print(f"  parsed+norm : {r.ok}   skipped: {r.skipped}   failed: {r.failed}")
    print(f"  elapsed     : {r.elapsed_ms:.0f} ms")
    print(f"  per format  : {r.per_format}")
    if r.errors:
        print(f"  errors({len(r.errors)})   first -> {r.errors[0]}")
    if r.ids:
        print(f"  doc ids     : {r.ids[:5]}")


def _embed_pass(report, store, cfg) -> None:
    from ..embedding import batch_embed, default_embedder
    from ..parser.dom import Document

    embed = default_embedder()
    total_texts = total_vecs = 0
    for did in report.ids:
        # normalized DOM lives at dom/<doc_id>/norm-{version}.docJSON (versioned)
        matches = sorted((store.root / "dom" / did).glob("norm-v*.docJSON"))
        if not matches:
            continue
        blob = matches[-1].read_bytes()
        doc = Document.model_validate_json(blob.decode("utf-8"))
        blocks = [b for p in doc.pages for b in p.blocks]
        total_texts += len(blocks)
        total_vecs += len(batch_embed(embed.embed, [b.text for b in blocks], cfg.embed_batch_size))
    device = getattr(embed, "device", "n/a")
    print(f"\n  batch-embedded: docs={len(report.ids)} blocks={total_texts} vecs={total_vecs} "
          f"(embedder={embed.name} device={device} dim={getattr(embed, 'dim', 'n/a')})")


def _write_report(out: str, report) -> None:
    import os
    p = os.path.join(out, "batch_report.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({
            "total": report.total, "ok": report.ok, "skipped": report.skipped,
            "failed": report.failed, "elapsed_ms": report.elapsed_ms,
            "per_format": report.per_format,
        }, fh, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())