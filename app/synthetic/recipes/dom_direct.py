"""Route B Direct DOM Factoid QA Recipe.

Bypasses knowledge graph extraction for narrative text blocks, directly generating
grounded factoid Q&A pairs from Canonical DOM content-addressed text chunks.
"""

from __future__ import annotations

from typing import Any

from app.kg.store.store import DuckDBGraphStore
from app.synthetic.quality.triad import GRAQualityTriad
from app.synthetic.schema import GroundingSubgraph, ProvenancePointer, QualityMetadata, SyntheticRecord


class DOMDirectRecipe:
    """Recipe for generating factoid QA directly from DOM text chunks (Route B)."""

    @property
    def name(self) -> str:
        return "dom_direct"

    def generate(
        self,
        store: DuckDBGraphStore,
        triad: GRAQualityTriad,
        count: int = 10,
        **kwargs: Any,
    ) -> list[SyntheticRecord]:
        chunks: list[dict[str, Any]] = kwargs.get("chunks", [])

        # If no explicit chunks passed, sample evidence verbatim texts from store
        if not chunks:
            ev_rows = store.con.execute(
                "SELECT doc_id, page_index, block_id, verbatim_text FROM evidence LIMIT ?",
                [count],
            ).fetchall()
            for r in ev_rows:
                chunks.append(
                    {
                        "doc_id": r[0],
                        "page_index": r[1],
                        "block_id": r[2],
                        "text": r[3],
                    }
                )

        records: list[SyntheticRecord] = []
        for chk in chunks[:count]:
            text = chk.get("text", "")
            if not text:
                continue

            doc_id = chk.get("doc_id", "unknown-doc")
            page_index = chk.get("page_index", 0)
            block_id = chk.get("block_id", "blk-0")
            bbox = chk.get("bbox")

            candidate = triad.generator.generate_from_chunk(text, doc_id=doc_id)
            refined_cand, _ = triad.evaluate_and_refine(candidate, text)

            if refined_cand is not None:
                provenance = [
                    ProvenancePointer(
                        doc_id=doc_id,
                        page_index=page_index,
                        block_id=block_id,
                        bbox=bbox,
                        source_text=text,
                    )
                ]
                record = SyntheticRecord(
                    task_type="factoid_qa",
                    domain="pharmacology",
                    instruction=refined_cand.instruction,
                    reasoning_trace=refined_cand.reasoning_trace,
                    target_response=refined_cand.target_response,
                    grounding_subgraph=GroundingSubgraph(nodes=[], edges=[]),
                    provenance=provenance,
                    quality_metadata=QualityMetadata(complexity_hops=1),
                )
                records.append(record)

        return records
