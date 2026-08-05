"""CLI for the semantic chunking module.

Usage:
    python -m app.chunking.cli --doc <doc_id> --store <root> [--embed] [--dom-key <key>]

Without ``--embed``: chunk-only (chunks artifact written), prints the report
summary. With ``--embed``: full `ChunkEmbedPipeline` run (chunks + embeddings),
prints the embedding summary. Missing DOM -> error line, exit 1.
"""
from __future__ import annotations

import argparse
import sys

from .pipeline import ChunkEmbedPipeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synthetic Data Factory — Semantic Chunking (DOM->chunks[->embeddings])")
    ap.add_argument("--doc", required=True, help="doc_id")
    ap.add_argument("--store", default="parser_out", help="store root dir")
    ap.add_argument("--embed", action="store_true", help="also project chunks to embeddings")
    ap.add_argument("--dom-key", default=None, help="explicit normalized DOM key (skips latest-version glob)")
    args = ap.parse_args(argv)

    pipe = ChunkEmbedPipeline(store_root=args.store)
    if args.embed:
        res = pipe.run(args.doc, args.dom_key)
        if res.status == "failed":
            print(f"FAIL {args.doc}: {res.error}", file=sys.stderr)
            return 1
        print(f"OK   {args.doc} chunks={res.chunks_created} embedded={res.embedded} "
              f"skipped={res.skipped} dim={res.dim} dtype={res.dtype} "
              f"embedder={pipe.embedder.name}")
        return 0

    res = pipe.chunk_only(args.doc, args.dom_key)
    if res.status == "failed":
        print(f"FAIL {args.doc}: {res.error}", file=sys.stderr)
        return 1
    print(f"OK   {args.doc} chunks={res.chunks_created} dom={res.dom_storage_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
