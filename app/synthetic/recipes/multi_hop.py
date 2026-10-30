"""Linear Multi-Hop Chain Chain-of-Thought (CoT) Recipe.

Generates complex multi-step clinical deduction questions and step-by-step
reasoning traces grounded in 2-4 hop knowledge graph paths.
"""

from __future__ import annotations

from typing import Any

from app.kg.store.store import DuckDBGraphStore
from app.synthetic.quality.triad import GRAQualityTriad
from app.synthetic.sampler import SubgraphTopologySampler
from app.synthetic.schema import GroundingSubgraph, ProvenancePointer, QualityMetadata, SyntheticRecord


class MultiHopRecipe:
    """Recipe for generating multi-hop clinical reasoning records."""

    @property
    def name(self) -> str:
        return "multi_hop"

    def generate(
        self,
        store: DuckDBGraphStore,
        triad: GRAQualityTriad,
        count: int = 10,
        **kwargs: Any,
    ) -> list[SyntheticRecord]:
        sampler = SubgraphTopologySampler(store)
        min_hops = kwargs.get("min_hops", 2)
        max_hops = kwargs.get("max_hops", 4)
        paths = sampler.sample_linear_chains(min_hops=min_hops, max_hops=max_hops, count=count)

        records: list[SyntheticRecord] = []

        for path in paths:
            # Collect evidence and build grounding context
            provenance_ptrs: list[ProvenancePointer] = []
            grounding_edges: list[dict[str, Any]] = []
            context_snippets: list[str] = []

            for step in path.steps:
                ast_dict = store.get_assertion_with_evidence(step.assertion_id)
                if ast_dict:
                    grounding_edges.append(
                        {
                            "subject": ast_dict["subject_id"],
                            "predicate": ast_dict["predicate"],
                            "object": ast_dict["object_id"],
                            "negation": ast_dict["negation"],
                            "population": ast_dict["population"],
                            "dosage": ast_dict["dosage"],
                        }
                    )
                    for ev in ast_dict.get("evidence", []):
                        provenance_ptrs.append(
                            ProvenancePointer(
                                doc_id=ev["doc_id"],
                                page_index=ev["page_index"],
                                block_id=ev["block_id"],
                                bbox=ev.get("bbox"),
                                source_text=ev["verbatim_text"],
                            )
                        )
                        context_snippets.append(f"[{ev['doc_id']}] {ev['verbatim_text']}")

            grounding_context = "\n".join(context_snippets) or f"Grounding path with {path.hops} hops."
            nodes = list(set([step.subject_id for step in path.steps] + [step.object_id for step in path.steps]))

            # Generate and refine candidate
            candidate = triad.generator.generate_from_path(path)
            refined_cand, assessment = triad.evaluate_and_refine(candidate, grounding_context)

            if refined_cand is not None:
                record = SyntheticRecord(
                    task_type="multi_hop_reasoning",
                    domain="pharmacology",
                    instruction=refined_cand.instruction,
                    reasoning_trace=refined_cand.reasoning_trace,
                    target_response=refined_cand.target_response,
                    grounding_subgraph=GroundingSubgraph(nodes=nodes, edges=grounding_edges),
                    provenance=provenance_ptrs,
                    quality_metadata=QualityMetadata(
                        complexity_hops=path.hops,
                        symbolic_execution_valid=True,
                    ),
                )
                records.append(record)

        return records
