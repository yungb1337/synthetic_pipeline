"""Chunk -> persist -> embed-only-missing -> persist projection stage.

`ChunkEmbedPipeline` is a standalone projection (parallel to parse->normalize,
NOT inside `ParseNormalizePipeline`). It resolves the latest normalized DOM for
a doc, chunks it, persists the chunks artifact, then embeds ONLY the missing
``chunk_id``s (never embed twice — structural: `chunk_id` has no embedder
dependence) under the token-budget batching policy, and writes a float32 matrix
+ sidecar with a per-doc validation stamp (ADR-010).

Embedder: `factory.default_embedder` (real BGE-M3 when available,
`DummyEmbedder` otherwise). Block-level `embed_document_blocks` is NEVER used
for chunks (architecture §3.8).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..embedding import batch_embed, default_embedder
from ..parser.dom import Document
from ..parser.events import EventPublisher, silent_sink
from .batching import group_by_token_budget
from .chunker import ChunkResult, SemanticChunker
from .config import ChunkingConfig
from .schema import ChunksArtifact
from .store import ChunkStore, FilesystemChunkStore, _sanitize_embedder_id, _version_key
from .tokenize import TokenCounter


@dataclass
class ChunkEmbedResult:
    doc_id: str
    status: str = "ok"            # "ok" | "failed"
    error: str = ""
    dom_storage_key: str = ""
    chunks_created: int = 0
    embedded: int = 0
    skipped: int = 0
    dim: int = 0
    dtype: str = ""
    ms: int = 0


class ChunkEmbedPipeline:
    """Chunk a doc and project its chunks to embeddings (idempotent)."""

    def __init__(
        self,
        store_root: str,
        chunk_store: ChunkStore | None = None,
        embedder=None,
        config: ChunkingConfig | None = None,
        events: EventPublisher | None = None,
    ):
        self.store_root = store_root
        self.chunk_store = chunk_store if chunk_store is not None else FilesystemChunkStore(store_root)
        self._embedder = embedder  # resolved lazily via `embedder` — see property
        self.config = config if config is not None else ChunkingConfig()
        self.events = events if events is not None else EventPublisher(sink=silent_sink())
        # one counter per pipeline instance — all chunking + batching share it
        self.counter = TokenCounter(
            mode=self.config.tokenizer_mode,
            tokenizer_path=self.config.tokenizer_path,
            allow_char4_fallback=self.config.allow_char4_fallback,
        )

    @property
    def embedder(self):
        """Resolve the embedder lazily on first use.

        Chunk-only runs (``chunk_only()`` / CLI without ``--embed``) never touch
        this property, so ``default_embedder()`` — the real BGE-M3 model — is
        never loaded into VRAM unless embeddings are actually being produced.
        """
        if self._embedder is None:
            self._embedder = default_embedder()
        return self._embedder

    def run(self, doc_id: str, dom_storage_key: str | None = None) -> ChunkEmbedResult:
        t0 = time.time()
        doc, dom_key = self._resolve_dom(doc_id, dom_storage_key)
        if doc is None:
            return ChunkEmbedResult(
                doc_id=doc_id, status="failed",
                error="no normalized DOM found (norm-v*.docJSON)",
                dom_storage_key=dom_key, ms=int((time.time() - t0) * 1000),
            )
        try:
            return self._run_ok(doc_id, doc, dom_key, t0)
        except Exception as e:  # never crash a batch on a bad doc (mirror DocResult)
            return ChunkEmbedResult(
                doc_id=doc_id, status="failed", error=str(e),
                dom_storage_key=dom_key, ms=int((time.time() - t0) * 1000),
            )

    def chunk_only(self, doc_id: str, dom_storage_key: str | None = None) -> ChunkEmbedResult:
        """Chunk + persist chunks WITHOUT touching the embedder (CLI chunk-only mode)."""
        t0 = time.time()
        doc, dom_key = self._resolve_dom(doc_id, dom_storage_key)
        if doc is None:
            return ChunkEmbedResult(
                doc_id=doc_id, status="failed",
                error="no normalized DOM found (norm-v*.docJSON)",
                dom_storage_key=dom_key, ms=int((time.time() - t0) * 1000),
            )
        result = SemanticChunker(self.config, self.counter).chunk(doc, dom_key)
        artifact = ChunksArtifact(
            schema_version="chunks-v1",
            doc_id=doc_id,
            chunker_version=self.config.chunker_version,
            dom_storage_key=dom_key,
            chunks=result.chunks,
            report=result.report,
        )
        self.chunk_store.put_chunks(doc_id, artifact)
        return ChunkEmbedResult(
            doc_id=doc_id, dom_storage_key=dom_key,
            chunks_created=len(result.chunks), ms=int((time.time() - t0) * 1000),
        )

    def _resolve_dom(self, doc_id: str, dom_storage_key: str | None) -> tuple[Document | None, str]:
        """Resolve the latest normalized DOM; returns (doc, storage key)."""
        if dom_storage_key:
            p = Path(self.store_root) / dom_storage_key
            if not p.exists():
                return None, dom_storage_key
            return Document.model_validate_json(p.read_text(encoding="utf-8")), dom_storage_key
        matches = sorted(
            (Path(self.store_root) / "dom" / doc_id).glob("norm-v*.docJSON"),
            key=lambda p: _version_key(p.stem[len("norm-v"):]),
        )
        if not matches:
            return None, ""
        p = matches[-1]
        return Document.model_validate_json(p.read_text(encoding="utf-8")), f"dom/{doc_id}/{p.name}"

    def _run_ok(self, doc_id: str, doc: Document, dom_key: str, t0: float) -> ChunkEmbedResult:
        config = self.config
        chunker = SemanticChunker(config, self.counter)
        result: ChunkResult = chunker.chunk(doc, dom_key)
        chunks = result.chunks
        artifact = ChunksArtifact(
            schema_version="chunks-v1",
            doc_id=doc_id,
            chunker_version=config.chunker_version,
            dom_storage_key=dom_key,
            chunks=chunks,
            report=result.report,
        )
        self.chunk_store.put_chunks(doc_id, artifact)     # persist before embedding

        if not chunks:
            return ChunkEmbedResult(
                doc_id=doc_id, dom_storage_key=dom_key, chunks_created=0,
                ms=int((time.time() - t0) * 1000),
            )

        groups = group_by_token_budget(
            chunks, self.counter, config.max_tokens_per_call, config.max_texts_per_call,
        )
        embedder_id = _sanitize_embedder_id(self.embedder.name)

        # never embed twice: presence keyed on content-addressed chunk_id
        existing = self.chunk_store.get_embeddings(doc_id, config.chunker_version, embedder_id)
        existing_map: dict = {}
        if existing is not None:
            existing_ids, existing_matrix, _ = existing
            existing_map = dict(zip(existing_ids, existing_matrix))
        missing = [c for c in chunks if c.chunk_id not in existing_map]

        new_map: dict = {}
        for group in groups:
            missing_group = [c for c in group if c.chunk_id not in existing_map]
            if not missing_group:
                continue
            texts = [c.text for c in missing_group]
            vecs = batch_embed(self.embedder.embed, texts, batch_size=len(texts))
            for c, v in zip(missing_group, vecs):
                new_map[c.chunk_id] = v

        # full matrix in chunk_id order = doc order (row order = artifact order).
        # NOTE: dict.get(key, default) evaluates the default eagerly, so do NOT
        # use it here — the never-embed-twice re-run has empty new_map and every
        # chunk in existing_map.
        rows = []
        for c in chunks:
            if c.chunk_id in existing_map:
                rows.append(list(existing_map[c.chunk_id]))
            else:
                rows.append(list(new_map[c.chunk_id]))
        matrix = np.asarray(rows, dtype="float32")

        # ADR-010 validation stamp: re-embed chunks[0], cosine vs stored row
        validation: dict = {}
        sample_vec = batch_embed(self.embedder.embed, [chunks[0].text], batch_size=1)[0]
        cosine = _cosine(sample_vec, [float(x) for x in matrix[0]])
        validation = {"sample_chunk_id": chunks[0].chunk_id, "cosine": round(cosine, 6), "ok": cosine >= 0.9999}

        dim = matrix.shape[1]
        dtype = str(matrix.dtype)
        meta = {
            "embedder_id": embedder_id,
            "chunker_version": config.chunker_version,
            "dim": dim,
            "dtype": dtype,
            "normalize": True,
            "device": getattr(self.embedder, "device", "n/a"),
            "max_tokens_per_call": config.max_tokens_per_call,
            "max_texts_per_call": config.max_texts_per_call,
            "validation": validation,
        }
        sidecar_key = self.chunk_store.put_embeddings(
            doc_id, config.chunker_version, embedder_id,
            [c.chunk_id for c in chunks], matrix, meta,
        )

        # rewrite the chunks artifact with embedding_ref filled (chunk_id unchanged)
        filled = artifact.model_copy(
            update={"chunks": [c.model_copy(update={"embedding_ref": sidecar_key}) for c in chunks]}
        )
        self.chunk_store.put_chunks(doc_id, filled)

        ms = int((time.time() - t0) * 1000)
        self.events.emit("chunk_embedded.v1", {
            "doc_id": doc_id,
            "chunker_version": config.chunker_version,
            "embedder_id": embedder_id,
            "chunks": len(chunks),
            "embedded": len(missing),
            "skipped": len(chunks) - len(missing),
            "dim": dim,
            "dtype": dtype,
            "ms": ms,
        })
        return ChunkEmbedResult(
            doc_id=doc_id,
            dom_storage_key=dom_key,
            chunks_created=len(chunks),
            embedded=len(missing),
            skipped=len(chunks) - len(missing),
            dim=dim,
            dtype=dtype,
            ms=ms,
        )


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two (L2-normalized or not) vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
