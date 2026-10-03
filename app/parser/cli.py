"""CLI for the parser module (Extraction -> DOM).

Usage:
    python -m app.parser.cli --in FILE|DIR [--out DIR] [--no-ocr]

Processes a file (or every file under a directory), writes raw + DOM + images
to the Store root, and prints a per-document summary + the parsed event.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from .config import default_config
from .events import EventPublisher
from .extraction import Extractor, set_shared_scheduler
from .scheduler import Scheduler
from .storage import FilesystemStore
from .storage_pages import Ledger, PageStore


def _file_types():
    return (
        ".pdf",
        ".docx",
        ".xlsx",
        ".csv",
        ".tsv",
        ".json",
        ".xml",
        ".html",
        ".md",
        ".markdown",
        ".txt",
        ".png",
        ".jpg",
        ".jpeg",
        ".tiff",
        ".gif",
    )


def _shard_files(
    files: list[Path], shard_index: int = 0, shard_total: int = 1
) -> list[Path]:
    """B1/B3: Partition files deterministically by name hash across shards."""
    if shard_total <= 1:
        return files
    if not (0 <= shard_index < shard_total):
        raise ValueError(
            f"shard_index {shard_index} out of bounds for shard_total {shard_total}"
        )
    import hashlib

    out = []
    for f in files:
        h = int(hashlib.sha256(f.name.encode("utf-8")).hexdigest()[:8], 16)
        if (h % shard_total) == shard_index:
            out.append(f)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Synthetic Data Factory — Parser (Extraction->DOM)"
    )
    ap.add_argument("--in", dest="input", required=True, help="input file or directory")
    ap.add_argument("--out", dest="out", default="parser_out", help="store root dir")
    ap.add_argument("--no-ocr", action="store_true", help="disable OCR")
    ap.add_argument("--native-concurrency", type=int, default=None)
    ap.add_argument("--heavy-concurrency", type=int, default=None)
    ap.add_argument(
        "--in-process",
        action="store_true",
        default=True,
        help="run heavy engine in-process (single thread, stable)",
    )
    ap.add_argument(
        "--no-in-process",
        action="store_false",
        dest="in_process",
        help="run heavy engine in subprocess pool",
    )
    ap.add_argument("--limit", type=int, default=0, help="max files parsed (0=all)")
    ap.add_argument(
        "--offset", type=int, default=0, help="starting file index (0=first)"
    )
    ap.add_argument(
        "--shard-index", type=int, default=0, help="shard index (0 to shard-total-1)"
    )
    ap.add_argument("--shard-total", type=int, default=1, help="total number of shards")
    args = ap.parse_args(argv)

    cfg = default_config()
    if args.no_ocr:
        cfg = replace(cfg, ocr_enabled=False)
    store = FilesystemStore(args.out)
    # CLI: run heavy pages via bounded in-process single thread (or ProcessPool if requested)
    page_store = PageStore(str(store.root))
    ledger = Ledger(str(store.root))
    scheduler = Scheduler(
        cfg,
        native_concurrency=args.native_concurrency,
        heavy_concurrency=args.heavy_concurrency,
        page_store=page_store,
        ledger=ledger,
        prefer_in_process_heavy=args.in_process,
    )
    set_shared_scheduler(scheduler)
    try:
        extractor = Extractor(
            cfg,
            store,
            events=EventPublisher(),
            scheduler=scheduler,
            page_store=page_store,
            ledger=ledger,
        )

        path = Path(args.input)
        files = (
            [path]
            if path.is_file()
            else sorted(p for p in path.rglob("*") if p.suffix.lower() in _file_types())
        )
        if args.shard_total > 1:
            files = _shard_files(
                files, shard_index=args.shard_index, shard_total=args.shard_total
            )
        if args.offset:
            files = files[args.offset :]
        if args.limit:
            files = files[: args.limit]

        if not files:
            print("no supported files found", file=sys.stderr)
            return 2

        ok = 0
        for f in files:
            try:
                data = f.read_bytes()
                outcome = extractor.extract(data, filename=f.name, resume=True)
                if outcome.ok:
                    ok += 1
                    print(
                        f"OK   {f.name:28} {outcome.detected.slug:10} pages={len(outcome.document.pages):<3} "
                        f"blocks={outcome.report['blocks']:<4} tables={outcome.report['tables']:<3} "
                        f"route={outcome.report.get('route') or '-'}",
                        flush=True,
                    )
                    t = outcome.report.get("timings") or {}
                    parts = "  ".join(f"{k}={v}ms" for k, v in t.items())
                    print(
                        f"      timings: {parts}  total={outcome.report['elapsed_ms']}ms",
                        flush=True,
                    )
                else:
                    print(f"SKIP {f.name:28} {outcome.status}", flush=True)
            except Exception as exc:  # noqa: BLE001 - containment across batch files
                print(
                    f"FAIL {f.name:28} {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

        print(
            f"\nparsed {ok}/{len(files)} documents -> store under {args.out}",
            flush=True,
        )
        return 0
    finally:
        set_shared_scheduler(None)
        scheduler.close()


if __name__ == "__main__":
    raise SystemExit(main())
