"""Storage for chunking outputs — the retrieval seam (interface-only this run).

`ChunkStore` mirrors the parser's `Store` (versioned per `doc_id` x version,
same-version deterministic overwrite, prior versions retained — ADR #8):

    root/
      chunks/{doc_id}/chunks-v{chunker_version}.json
      embeddings/{doc_id}/emb-v{chunker_version}-{embedder_id}.npy   # float32 matrix, N x dim
      embeddings/{doc_id}/emb-v{chunker_version}-{embedder_id}.json  # sidecar: meta + chunk_ids row order

`iter_*` is what a future vector store / hybrid retrieval consumes; no vector
index is built this run. `_version_suffix` is imported from `app.parser.storage`
(pure function; chunking already depends on `app.parser.dom`, not an internals
leak).
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator

import numpy as np

from ..parser.storage import _version_suffix
from .schema import ChunksArtifact

# embedder_id sanitize charset (architecture §3.6): filesystem-safe.
_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"


def _sanitize_embedder_id(name: str) -> str:
    """Filesystem-safe embedder key.

    Replace any char outside ``[A-Za-z0-9._-]`` with ``_``. The path separator
    ``/`` (model org/name separator) becomes ``__`` so the org/name boundary
    stays visible in the key (plan B7: "BAAI/bge-m3@local-fp16" ->
    "BAAI__bge-m3_local-fp16"). Deterministic.
    """
    out = []
    for c in name:
        if c in _SAFE:
            out.append(c)
        elif c == "/":
            out.append("__")
        else:
            out.append("_")
    return "".join(out)


def _version_key(v: str) -> tuple:
    """Numeric version sort key (``vX.Y.Z`` -> ``((0, X), (0, Y), (0, Z))``).

    Robust to mixed numeric/non-numeric parts (e.g. ``v0.2.0-alpha``): each
    part becomes ``(0, int)`` when all-digits else ``(1, str)``, so any two
    version strings are always comparable (numeric sorts before non-numeric).
    Previously an all-int tuple vs a str tuple raised ``TypeError`` in
    ``sorted()`` (bugfix, 2026-08-05, surfaced by the module walkthrough).
    """
    out = []
    for p in v.split("."):
        if p.isdigit():
            out.append((0, int(p)))
        else:
            out.append((1, p))
    return tuple(out)


class ChunkStore(ABC):
    """Seam between chunking/embedding and persistence + future retrieval."""

    # chunks
    @abstractmethod
    def put_chunks(self, doc_id: str, artifact: ChunksArtifact) -> str: ...
    @abstractmethod
    def get_chunks(self, doc_id: str, chunker_version: str) -> ChunksArtifact | None: ...
    @abstractmethod
    def latest_chunks(self, doc_id: str) -> ChunksArtifact | None: ...
    @abstractmethod
    def iter_all_chunks(self) -> Iterator[ChunksArtifact]: ...

    # embeddings
    @abstractmethod
    def put_embeddings(
        self, doc_id: str, chunker_version: str, embedder_id: str,
        chunk_ids: list[str], matrix: np.ndarray, meta: dict,
    ) -> str: ...
    @abstractmethod
    def get_embeddings(
        self, doc_id: str, chunker_version: str, embedder_id: str,
    ) -> tuple[list[str], np.ndarray, dict] | None: ...
    @abstractmethod
    def get_embedding(
        self, doc_id: str, chunk_id: str, chunker_version: str, embedder_id: str,
    ) -> list[float] | None: ...
    @abstractmethod
    def iter_embeddings(self) -> Iterator[tuple[str, list[str], np.ndarray, dict]]: ...


class FilesystemChunkStore(ChunkStore):
    """Versioned per-doc layout under ``root`` (ADR #8 semantics)."""

    def __init__(self, root: str):
        self.root = Path(root)
        (self.root / "chunks").mkdir(parents=True, exist_ok=True)
        (self.root / "embeddings").mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- chunks
    def _chunks_path(self, doc_id: str, version: str) -> Path:
        return self.root / "chunks" / doc_id / f"chunks-{version}.json"

    def put_chunks(self, doc_id: str, artifact: ChunksArtifact) -> str:
        version = _version_suffix(artifact.chunker_version)
        p = self._chunks_path(doc_id, version)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
        return f"chunks/{doc_id}/chunks-{version}.json"

    def get_chunks(self, doc_id: str, chunker_version: str) -> ChunksArtifact | None:
        p = self._chunks_path(doc_id, _version_suffix(chunker_version))
        if not p.exists():
            return None
        return ChunksArtifact.model_validate_json(p.read_text(encoding="utf-8"))

    def latest_chunks(self, doc_id: str) -> ChunksArtifact | None:
        matches = sorted(
            (self.root / "chunks" / doc_id).glob("chunks-v*.json"),
            key=lambda p: _version_key(p.stem[len("chunks-v"):]),
        )
        if not matches:
            return None
        return ChunksArtifact.model_validate_json(matches[-1].read_text(encoding="utf-8"))

    def iter_all_chunks(self) -> Iterator[ChunksArtifact]:
        for doc_dir in sorted(self.root.glob("chunks/*"), key=lambda p: p.name):
            for p in sorted(doc_dir.glob("chunks-v*.json"), key=lambda q: q.name):
                yield ChunksArtifact.model_validate_json(p.read_text(encoding="utf-8"))

    # ------------------------------------------------------------ embeddings
    def _emb_paths(self, doc_id: str, version: str, embedder_id: str) -> tuple[Path, Path]:
        # build filenames explicitly — the version already carries the leading
        # "v" (suffix form) and contains dots, so `with_suffix` on a dotted
        # base would mangle the name.
        d = self.root / "embeddings" / doc_id
        stem = f"emb-{version}-{embedder_id}"
        return d / f"{stem}.npy", d / f"{stem}.json"

    def put_embeddings(
        self, doc_id: str, chunker_version: str, embedder_id: str,
        chunk_ids: list[str], matrix: np.ndarray, meta: dict,
    ) -> str:
        version = _version_suffix(chunker_version)
        npy_path, json_path = self._emb_paths(doc_id, version, embedder_id)
        npy_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(npy_path, np.asarray(matrix, dtype=np.float32))
        sidecar = dict(meta)
        sidecar["chunk_ids"] = chunk_ids
        sidecar["npy_key"] = f"embeddings/{doc_id}/{npy_path.name}"
        json_path.write_text(json.dumps(sidecar, sort_keys=True, indent=2, default=str), encoding="utf-8")
        return f"embeddings/{doc_id}/{json_path.name}"

    def get_embeddings(
        self, doc_id: str, chunker_version: str, embedder_id: str,
    ) -> tuple[list[str], np.ndarray, dict] | None:
        npy_path, json_path = self._emb_paths(doc_id, _version_suffix(chunker_version), embedder_id)
        if not json_path.exists() or not npy_path.exists():
            return None
        sidecar = json.loads(json_path.read_text(encoding="utf-8"))
        chunk_ids = sidecar["chunk_ids"]
        matrix = np.load(npy_path)
        return chunk_ids, matrix, sidecar

    def get_embedding(
        self, doc_id: str, chunk_id: str, chunker_version: str, embedder_id: str,
    ) -> list[float] | None:
        got = self.get_embeddings(doc_id, chunker_version, embedder_id)
        if got is None:
            return None
        chunk_ids, matrix, _ = got
        try:
            row = chunk_ids.index(chunk_id)
        except ValueError:
            return None
        return [float(x) for x in matrix[row]]

    def iter_embeddings(self) -> Iterator[tuple[str, list[str], np.ndarray, dict]]:
        for doc_dir in sorted(self.root.glob("embeddings/*"), key=lambda p: p.name):
            for json_path in sorted(doc_dir.glob("emb-v*.json"), key=lambda q: q.name):
                sidecar = json.loads(json_path.read_text(encoding="utf-8"))
                npy_path = json_path.with_suffix(".npy")
                if not npy_path.exists():
                    continue
                yield doc_dir.name, sidecar["chunk_ids"], np.load(npy_path), sidecar
