"""Tabular Aggregation & Comparative Reasoning Recipe.

Formulates cross-row and cross-column clinical comparative QA pairs from
relational assertions derived from Canonical DOM Table structures.
"""

from __future__ import annotations

from typing import Any

from app.kg.store.store import DuckDBGraphStore
from app.synthetic.quality.generator_agent import CandidateGeneration
from app.synthetic.quality.triad import GRAQualityTriad
from app.synthetic.schema import GroundingSubgraph, ProvenancePointer, QualityMetadata, SyntheticRecord


class TableReasoningRecipe:
    """Recipe for generating structured tabular comparative QA pairs."""

    @property
    def name(self) -> str:
        return "table_reasoning"

    def generate(
        self,
        store: DuckDBGraphStore,
        triad: GRAQualityTriad,
        count: int = 10,
        **kwargs: Any,
    ) -> list[SyntheticRecord]:
        # Query assertions extracted from tables
        table_ev_rows = store.con.execute(
            """
            SELECT DISTINCT a.id
            FROM assertions a
            JOIN evidence ev ON a.id = ev.assertion_id
            WHERE ev.block_id LIKE 'tbl-%' OR a.guideline_source = 'Table'
            LIMIT 50
            """
        ).fetchall()
        ast_ids = [r[0] for r in table_ev_rows]

        if not ast_ids:
            # Fall back to general assertions if no table-specific ones are loaded
            gen_rows = store.con.execute("SELECT id FROM assertions LIMIT 20").fetchall()
            ast_ids = [r[0] for r in gen_rows]

        records: list[SyntheticRecord] = []
        batch_size = min(3, len(ast_ids))
        if batch_size == 0:
            return records

        for i in range(0, min(len(ast_ids), count * batch_size), batch_size):
            cluster_ids = ast_ids[i : i + batch_size]
            cluster_details = [store.get_assertion_with_evidence(cid) for cid in cluster_ids]
            valid_details = [c for c in cluster_details if c]

            if not valid_details:
                continue

            # Formulate comparative prompt
            summary_lines = []
            provenance_ptrs: list[ProvenancePointer] = []
            nodes: set[str] = set()
            edges: list[dict[str, Any]] = []

            for item in valid_details:
                nodes.add(item["subject_id"])
                nodes.add(item["object_id"])
                edges.append(
                    {
                        "subject": item["subject_id"],
                        "predicate": item["predicate"],
                        "object": item["object_id"],
                        "population": item["population"],
                        "dosage": item["dosage"],
                    }
                )
                summary_lines.append(
                    f"- {item['subject_id']} -> {item['predicate']} -> {item['object_id']} "
                    f"[Pop: {item['population']}, Dose: {item['dosage']}]"
                )
                for ev in item.get("evidence", []):
                    provenance_ptrs.append(
                        ProvenancePointer(
                            doc_id=ev["doc_id"],
                            page_index=ev["page_index"],
                            block_id=ev["block_id"],
                            bbox=ev.get("bbox"),
                            source_text=ev["verbatim_text"],
                        )
                    )

            table_context = "\n".join(summary_lines)
            prompt = (
                f"You are a clinical pharmacologist analyzing structured table data:\n"
                f"{table_context}\n\n"
                f"Synthesize a comparative clinical question comparing dosage adjustments or indications "
                f"across these items, with reasoning trace in <think> ... </think> and authoritative conclusion.\n\n"
                f"Format:\n"
                f"INSTRUCTION: <comparative question>\n"
                f"<think>\n<step-by-step reasoning>\n</think>\n"
                f"RESPONSE: <final comparative answer>"
            )

            raw_out = triad.generator.llm_client.generate(
                prompt=prompt,
                system_prompt="You formulate rigorous table comparative QA.",
                temperature=0.2,
            )
            parsed = triad.generator._parse_output(
                raw_out, default_instruction="Compare clinical parameters across table rows."
            )

            refined_cand, _ = triad.evaluate_and_refine(parsed, table_context)
            if refined_cand is not None:
                record = SyntheticRecord(
                    task_type="table_qa",
                    domain="pharmacology",
                    instruction=refined_cand.instruction,
                    reasoning_trace=refined_cand.reasoning_trace,
                    target_response=refined_cand.target_response,
                    grounding_subgraph=GroundingSubgraph(nodes=list(nodes), edges=edges),
                    provenance=provenance_ptrs,
                    quality_metadata=QualityMetadata(complexity_hops=2),
                )
                records.append(record)
                if len(records) >= count:
                    break

        return records
