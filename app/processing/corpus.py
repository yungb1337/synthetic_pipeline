"""Corpus scan + incremental 'done' manifest.

The manifest is the idempotency backbone for millions-scale runs: a content
hash per file, persisted to JSON. A re-run only processes files whose hash is
NOT yet in the manifest — a resume after a crash or a weekly incremental pass
touch only new/changed files.
"""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DocRef:
    path: str
    name: str
    size: int
    sha256: str


def _hash_file(path: str, chunk: int = 1 << 20) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            size += len(b)
            h.update(b)
    return h.hexdigest(), size


def scan_dir(root: str, exts: tuple[str, ...]) -> list[str]:
    rp = Path(root)
    return [str(p) for p in rp.rglob("*")
            if p.is_file() and p.suffix.lower() in exts]


def hash_paths(paths: list[str], concurrency: int | None = None) -> list[DocRef]:
    """Content-hash many files in parallel (CPU/IO batched)."""
    concurrency = concurrency or min(16, (os.cpu_count() or 4) + 1)
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(_hash_one, p): p for p in paths}
        out = []
        for fut in futures:
            out.append(fut.result())
    return out


def _hash_one(path: str) -> DocRef:
    p = Path(path)
    try:
        sha, size = _hash_file(p)
    except OSError:
        sha, size = "", 0
    return DocRef(path=path, name=p.name, size=size, sha256=sha)


def _hash_file(p: Path) -> tuple[str, int]:
    return _hash_bytes(p.read_bytes())


def _hash_bytes(data: bytes) -> tuple[str, int]:
    return hashlib.sha256(data).hexdigest(), len(data)


def load_manifest(path: str) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data)
    except Exception:
        return set()


def save_manifest(path: str, shas: set[str]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(sorted(shas)), encoding="utf-8")


def pending(ref: DocRef, manifest: set[str]) -> bool:
    return bool(ref.sha256) and ref.sha256 not in manifest