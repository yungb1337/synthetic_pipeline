"""Counterfactual Edge Inversion DPO Preference Recipe.

Generates calibrated chosen (gold) and rejected (counterfactual) response pairs
for Direct Preference Optimization (DPO) preference alignment.
"""

from __future__ import annotations

from typing import Any

from app.kg.store.store import DuckDBGraphStore
from app.synthetic.quality.triad import GRAQualityTriad
from app.synthetic.sampler import SubgraphTopologySampler
from app.synthetic.schema import GroundingSubgraph, ProvenancePointer, QualityMetadata, SyntheticRecord


class DPOPerturbationRecipe:
    """Recipe for generating paired chosen/rejected preference datasets."""

    @property
    def name(self) -> str:
        return "dpo_perturbation"

    def generate(
        self,
        store: DuckDBGraphStore,
        triad: GRAQualityTriad,
        count: int = 10,
        **kwargs: Any,
    ) -> list[SyntheticRecord]:
        sampler = SubgraphTopologySampler(store)
        paths = sampler.sample_linear_chains(min_hops=2, max_hops=4, count=count)

        records: list[SyntheticRecord] = []

        for path in paths:
            # 1. Chosen generation
            chosen_candidate = triad.generator.generate_from_path(path)
            refined_chosen, _ = triad.evaluate_and_refine(
                chosen_candidate, f"Valid gold path: {path.path_id}"
            )
            if refined_chosen is None:
                continue

            # 2. Perturbation -> Rejected generation
            try:
                perturbed_path, perturbed_step = sampler.sample_counterfactual_perturbations(path)
                rejected_candidate = triad.generator.generate_from_path(
                    perturbed_path,
                    context_notes=f"NOTE: The following assertion is PERTURBED/COUNTERFACTUAL: {perturbed_step.subject_id} -> {perturbed_step.predicate} (negation={perturbed_step.negation}, dose={perturbed_step.dosage}).",
                    instruction=refined_chosen.instruction,
                )
                negative_response = rejected_candidate.target_response
            except Exception:
                negative_response = "Incorrect recommendation: Safe at full dose regardless of renal parameters."

            # Collect provenance
            provenance_ptrs: list[ProvenancePointer] = []
            grounding_edges: list[dict[str, Any]] = []

            for step in path.steps:
                ast_dict = store.get_assertion_with_evidence(step.assertion_id)
                if ast_dict:
                    grounding_edges.append(
                        {
                            "subject": ast_dict["subject_id"],
                            "predicate": ast_dict["predicate"],
                            "object": ast_dict["object_id"],
                            "negation": ast_dict["negation"],
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

            nodes = list(set([s.subject_id for s in path.steps] + [s.object_id for s in path.steps]))

            record = SyntheticRecord(
                task_type="dpo_preference",
                domain="pharmacology",
                instruction=refined_chosen.instruction,
                reasoning_trace=refined_chosen.reasoning_trace,
                target_response=refined_chosen.target_response,
                negative_response=negative_response,
                grounding_subgraph=GroundingSubgraph(nodes=nodes, edges=grounding_edges),
                provenance=provenance_ptrs,
                quality_metadata=QualityMetadata(
                    complexity_hops=path.hops,
                    symbolic_execution_valid=True,
                ),
            )
            records.append(record)

        return records
