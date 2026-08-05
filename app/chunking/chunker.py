"""Semantic chunking — DOM-anchored, deterministic (Module #3 core).

``SemanticChunker.chunk(doc)`` is a PURE function of ``(Document,
ChunkingConfig, TokenCounter)``: no I/O, no RNG, no embedder dependency. It
walks ``Document.reading_order``, cuts only between ``Block`` boundaries, merges
small blocks to a ~400-token budget, sentence-splits oversized blocks (> 2048,
the hard cap) under the current heading anchor, and applies a ~48-token
sentence-aligned overlap ONLY at heading seams (attributed via
``overlap_source_chunk_id``).

Determinism is structural: every step is total and order-stable, and ``chunk_id``
is content-addressed over ``(doc_id, text, source_block_ids)`` (plus a
``piece_index`` discriminator for sentence-split/force-split pieces of an
oversized block, so byte-identical pieces stay distinct) — the same DOM + config
+ tokenizer yields byte-identical chunk JSON.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..parser.dom import Document
from .config import ChunkingConfig
from .schema import Chunk, ChunkProvenance, compute_chunk_id
from .sentences import split_sentences, tail_sentences
from .tokenize import TokenCounter


@dataclass
class ChunkResult:
    """Pure output of a chunking pass — no I/O side effects."""

    chunks: list[Chunk]
    report: dict
    dom_storage_key: str


class SemanticChunker:
    """Turn a normalized DOM into content-addressed, lineage-carrying chunks."""

    def __init__(self, config: ChunkingConfig, counter: TokenCounter):
        self.config = config
        self.counter = counter

    # ------------------------------------------------------------------ public
    def chunk(self, doc: Document, dom_storage_key: str = "") -> ChunkResult:
        config = self.config
        items, order_info = self._resolve_order(doc)
        report: dict = {
            "blocks_seen": len(items),
            "blocks_missing": order_info["missing"],
            "blocks_orphaned": order_info["orphans"],
            "blocks_skipped_empty": 0,
            "chunks_created": 0,
            "forced_splits": 0,
            "overlap_chunks": 0,
            "split_ambiguous": 0,
            "tokens_total": 0,
            "order_source_used": order_info["used"],
            "warnings": list(order_info["warnings"]),
        }

        chunks: list[Chunk] = []
        seq = 0
        prev_chunk: Chunk | None = None

        # state of the currently open chunk
        open_blocks: list = []
        open_source = "reading_order"
        current_anchor = ""
        overlap_text = ""
        overlap_src: str | None = None

        def close_open() -> None:
            """Emit the open chunk (if any) and clear it."""
            nonlocal seq, prev_chunk, open_blocks, overlap_text, overlap_src
            if not open_blocks:
                return
            blocks = open_blocks
            source = open_source
            c = self._build_chunk(
                doc,
                text_parts=[b.text for b in blocks],
                source_block_ids=[b.id for b in blocks],
                pages=[b.page for b in blocks],
                source=source,
                anchor=current_anchor,
                seq=seq,
                forced_split=False,
                dom_storage_key=dom_storage_key,
                kind=blocks[0].kind if len(blocks) == 1 else "mixed",
                overlap_text=overlap_text,
                overlap_source_chunk_id=overlap_src,
            )
            chunks.append(c)
            seq += 1
            prev_chunk = c
            if overlap_src:
                report["overlap_chunks"] += 1
            open_blocks = []
            overlap_text = ""
            overlap_src = None

        def open_at(block, source: str) -> None:
            """Open a fresh chunk at ``block``."""
            nonlocal open_blocks, open_source, overlap_text, overlap_src
            open_blocks = [block]
            open_source = source
            overlap_text = ""
            overlap_src = None

        for block, source in items:
            text = block.text or ""
            if not text.strip():
                report["blocks_skipped_empty"] += 1
                continue
            if self.counter.count(text) > config.hard_max_tokens:
                # oversized block: close the open chunk, sentence-split, resume fresh
                close_open()
                pieces, amb = self._oversized_pieces(block)
                report["split_ambiguous"] += amb
                for piece_idx, (text_parts, forced) in enumerate(pieces):
                    c = self._build_chunk(
                        doc,
                        text_parts=text_parts,
                        source_block_ids=[block.id],
                        pages=[block.page],
                        source=source,
                        anchor=current_anchor,
                        seq=seq,
                        forced_split=forced,
                        piece_index=piece_idx,
                        dom_storage_key=dom_storage_key,
                        kind=block.kind,
                    )
                    chunks.append(c)
                    seq += 1
                    prev_chunk = c
                    if forced:
                        report["forced_splits"] += 1
                continue
            if block.kind == "heading":
                close_open()
                current_anchor = block.text
                # overlap only at heading seams (architecture §3.3)
                ot, osrc = "", None
                if config.overlap_at_heading_seams and prev_chunk is not None and prev_chunk.text:
                    tail = tail_sentences(prev_chunk.text, self.counter, config.overlap_tokens)
                    if tail:
                        ot = "\n".join(tail)
                        osrc = prev_chunk.chunk_id
                open_at(block, source)
                overlap_text = ot
                overlap_src = osrc
                continue
            # non-heading block
            if not open_blocks:
                open_at(block, source)
                continue
            cur = self.counter.count("\n".join(b.text for b in open_blocks))
            joined = self.counter.count("\n".join([*[b.text for b in open_blocks], text]))
            if joined <= config.target_tokens or (
                cur < config.min_band_tokens and joined <= config.soft_max_tokens
            ):
                open_blocks.append(block)
            else:
                close_open()
                open_at(block, source)

        close_open()

        # plan formula: the primary walk source that actually produced chunks
        sources = {c.order_source for c in chunks}
        if "reading_order" in sources:
            report["order_source_used"] = "reading_order"
        elif "page_order" in sources:
            report["order_source_used"] = "page_order"
        else:
            report["order_source_used"] = "orphan"
        report["chunks_created"] = len(chunks)
        report["tokens_total"] = sum(c.token_count for c in chunks)

        return ChunkResult(chunks=chunks, report=report, dom_storage_key=dom_storage_key)

    # ----------------------------------------------------------- order resolve
    def _resolve_order(self, doc: Document) -> tuple[list, dict]:
        """Deterministic block stream + lineage diagnostics.

        Returns ``(items, info)`` where ``items`` is ``[(Block, source)]`` with
        source in {"reading_order", "page_order", "orphan"}.
        """
        pages = sorted(doc.pages, key=lambda p: p.index)
        id_to_block = {}
        duplicates = 0
        for p in pages:
            for b in p.blocks:
                if b.id in id_to_block:
                    duplicates += 1
                else:
                    id_to_block[b.id] = b
        warnings = []
        if duplicates:
            warnings.append(f"{duplicates} duplicate block id(s) across pages; first instance wins")

        chain = doc.reading_order or []
        items: list = []
        if chain:
            missing = 0
            chain_seen: set = set()
            for bid in chain:
                b = id_to_block.get(bid)
                if b is None:
                    missing += 1
                    warnings.append(f"reading_order references missing block id {bid!r}; skipped")
                    continue
                chain_seen.add(bid)
                items.append((b, "reading_order"))
            # orphans: blocks in pages absent from the chain (hand-edited DOM)
            orphans = 0
            for p in pages:
                for b in p.blocks:
                    if b.id in chain_seen or id_to_block[b.id] is not b:
                        continue  # already consumed, or a duplicate instance
                    orphans += 1
                    items.append((b, "orphan"))
            return items, {"missing": missing, "orphans": orphans, "warnings": warnings, "used": "reading_order"}
        # fallback: page order (reading_order empty)
        for p in pages:
            for b in p.blocks:
                items.append((b, "page_order"))
        return items, {"missing": 0, "orphans": 0, "warnings": warnings, "used": "page_order"}

    # -------------------------------------------------------------- oversized
    def _oversized_pieces(self, block) -> tuple[list, int]:
        """Sentence-split an oversized block into ``(text_parts, forced)`` pieces.

        Sentences re-accumulate into sub-chunks capped at ``target_tokens``
        (NOT ``soft_max``). A single sentence exceeding ``hard_max_tokens``
        (pathological) is force-split with ``forced_split=True`` — recorded,
        never silent.
        """
        config = self.config
        sentences, amb = split_sentences(block.text or "")
        out: list = []
        parts: list = []
        for s in sentences:
            c = self.counter.count(s)
            if c > config.hard_max_tokens:
                if parts:
                    out.append((parts, False))
                    parts = []
                for piece in self._force_split(s, config.hard_max_tokens):
                    out.append(([piece], True))
                continue
            if parts and self.counter.count("\n".join([*parts, s])) > config.target_tokens:
                out.append((parts, False))
                parts = []
            parts.append(s)
        if parts:
            out.append((parts, False))
        return out, amb

    def _force_split(self, text: str, limit: int) -> list[str]:
        """Deterministic last-resort split for a degenerate single sentence.

        Recursive separator fallback: double-newline -> newline -> sentence-final
        punctuation, cutting AFTER the separator so pieces stay contiguous and
        lossless; hard midpoint as the final resort.
        """
        if self.counter.count(text) <= limit:
            return [text]
        for sep in ("\n\n", "\n", ".", "!", "?", "。", "！", "？", "…"):
            # a cut at position 0 or len(text) splits nothing useful (infinite-recurse guard)
            cuts = [c for c in self._cut_positions(text, sep) if 0 < c < len(text)]
            if not cuts:
                continue
            pieces = self._cut_at(text, cuts)
            if max(self.counter.count(p) for p in pieces) <= limit:
                return pieces
            out = []
            for p in pieces:
                out.extend(self._force_split(p, limit) if self.counter.count(p) > limit else [p])
            return out
        mid = len(text) // 2
        return self._force_split(text[:mid], limit) + self._force_split(text[mid:], limit)

    @staticmethod
    def _cut_positions(text: str, sep: str) -> list[int]:
        """Indices immediately AFTER each occurrence of ``sep``."""
        idxs = []
        start = 0
        while True:
            i = text.find(sep, start)
            if i < 0:
                break
            idxs.append(i + len(sep))
            start = i + len(sep)
        return idxs

    @staticmethod
    def _cut_at(text: str, cuts: list[int]) -> list[str]:
        """Contiguous substrings of ``text`` split at ascending cut indices."""
        pieces = []
        prev = 0
        for c in cuts:
            pieces.append(text[prev:c])
            prev = c
        if prev < len(text):
            pieces.append(text[prev:])
        return pieces

    # ---------------------------------------------------------------- builder
    def _build_chunk(
        self,
        doc: Document,
        text_parts: list[str],
        source_block_ids: list[str],
        pages: list[int],
        source: str,
        anchor: str,
        seq: int,
        forced_split: bool,
        dom_storage_key: str,
        kind: str,
        piece_index: int | None = None,
        overlap_text: str = "",
        overlap_source_chunk_id: str | None = None,
    ) -> Chunk:
        config = self.config
        text = "\n".join(text_parts)
        if overlap_text:
            text = overlap_text + "\n" + text
        pages = sorted(set(pages))
        provenance = ChunkProvenance(
            chunker_version=config.chunker_version,
            chunker_params=config.snapshot(),
            dom_schema_version=config.dom_schema_version,
            normalizer_version=doc.provenance.normalizer_version if doc.provenance else None,
            dom_storage_key=dom_storage_key,
            tokenizer=self.counter.tokenizer,
            tokenizer_ref_hash=self.counter.tokenizer_ref_hash,
            forced_split=forced_split,
        )
        return Chunk(
            chunk_id=compute_chunk_id(doc.document_id, text, source_block_ids, piece_index=piece_index),
            doc_id=doc.document_id,
            seq=seq,
            kind=kind,
            text=text,
            source_block_ids=source_block_ids,
            overlap_source_chunk_id=overlap_source_chunk_id,
            page=min(pages) if pages else 0,
            pages=pages,
            heading_anchor=anchor,
            token_count=self.counter.count(text),
            char_count=len(text),
            tokenizer=self.counter.tokenizer,
            order_source=source,
            provenance=provenance,
            embedding_ref="",
        )
