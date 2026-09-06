#!/usr/bin/env python
"""download_curated_corpus.py — download manifest PDFs with SHA256 verification.

Reads a corpus manifest (see seed_corpus.py), downloads every non-`ok` entry
into `<out>/<id>.pdf`, verifies it is a real PDF (magic bytes + SHA256), and
updates the manifest record {status, sha256, size_bytes, retrieved_at, error?}.
Resumable: re-running skips `ok` entries unless --force. Every failure is
recorded in the manifest (status:error) AND streamed to logs/download.log —
nothing is silently dropped.

Usage:
    .venv/Scripts/python.exe scripts/download_curated_corpus.py \
        --manifest <sources/manifest.json> --out <sources/pdf>
    options: --limit N | --only STRATA,S1 | --force | --no-verify-sha | --delay S

Dependencies: requests (installed)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

UA = "MedFactory-CorpusCuration/1.0 (public non-PHI open-access corpus; contact: repo owner)"
MAGIC = b"%PDF"
LOG_PATH = Path("logs/download.log")  # relative to repo root when run from repo root


def _log(line: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{ts} {line}\n")
    except OSError as exc:
        print(f"  [download] cannot write log: {exc}", file=sys.stderr)


def fetch(url: str, dest: Path, timeout: int = 90, retries: int = 3,
          backoff: float = 5.0, delay: float = 0.0) -> dict:
    """Download url -> dest; return {ok, sha256, size_bytes, error?, used_header_cd?}."""
    if delay:
        time.sleep(delay)
    last_err = "unknown"
    for attempt in range(retries):
        try:
            with requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": UA},
                              allow_redirects=True) as r:
                r.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                h = hashlib.sha256()
                size = 0
                with dest.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        if chunk:
                            f.write(chunk)
                            h.update(chunk)
                            size += len(chunk)
                sha = h.hexdigest()
                if size == 0:
                    last_err = f"empty body ({size} bytes)"
                    dest.unlink(missing_ok=True)
                    raise ValueError(last_err)
                return {"ok": True, "sha256": sha, "size_bytes": size, "error": None}
        except Exception as exc:  # noqa: BLE001 — record & retry any failure
            last_err = f"{type(exc).__name__}: {exc}"
            dest.unlink(missing_ok=True)
            if attempt < retries - 1:
                _log(f"  retry {attempt + 1}/{retries} for {url} ({last_err})")
                time.sleep(backoff * (attempt + 1))
    return {"ok": False, "sha256": None, "size_bytes": 0, "error": last_err}


def is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, help="path to corpus manifest.json")
    ap.add_argument("--out", required=True, help="directory to write <id>.pdf files")
    ap.add_argument("--limit", type=int, default=0, help="max downloads this run (0=all)")
    ap.add_argument("--only", default="", help="comma-separated strata to include")
    ap.add_argument("--force", action="store_true", help="re-download even ok entries")
    ap.add_argument("--no-verify-sha", action="store_true", help="skip the magic-byte PDF check")
    ap.add_argument("--delay", type=float, default=0.0, help="sleep between downloads (rate limit)")
    args = ap.parse_args()

    mpath = Path(args.manifest)
    if not mpath.is_file():
        print(f"ERROR: manifest not found: {mpath}", file=sys.stderr)
        return 2
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    only = set(s.strip() for s in args.only.split(",") if s.strip())

    todo = [r for r in manifest
            if (not only or r.get("stratum") in only)
            and (args.force or r.get("status") != "ok")]
    if args.limit > 0:
        todo = todo[: args.limit]

    _log(f"[download] manifest={mpath} out={outdir} todo={len(todo)} "
         f"(force={args.force})")
    if not todo:
        print("Nothing to download (all ok, or filter empty).")
        return 0

    okn = errn = 0
    for i, rec in enumerate(todo, 1):
        pid = rec["id"]
        dest = outdir / f"{pid}.pdf"
        url = rec["url"]
        res = fetch(url, dest, delay=args.delay)
        if res["ok"] and (args.no_verify_sha or is_pdf(dest)):
            rec.update({"status": "ok", "sha256": res["sha256"],
                        "size_bytes": res["size_bytes"],
                        "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "error": None})
            okn += 1
            _log(f"[download] OK {i}/{len(todo)} {pid} {res['size_bytes']}B {url}")
        else:
            rec.update({"status": "error", "sha256": None, "size_bytes": 0,
                        "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "error": res["error"] or "not a PDF (magic bytes)"})
            errn += 1
            _log(f"[download] ERR {i}/{len(todo)} {pid} {rec['error']} {url}")
            dest.unlink(missing_ok=True)

    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"[download] DONE ok={okn} err={errn} total={len(todo)}")
    print(f"DONE: ok={okn} err={errn} (manifest saved to {mpath})")
    return 1 if errn and not okn else 0


if __name__ == "__main__":
    raise SystemExit(main())