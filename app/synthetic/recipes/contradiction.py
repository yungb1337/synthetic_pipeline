"""Adversarial Premise-Contradiction Recipe.

Generates adversarial prompts asserting false clinical premises (e.g., claiming a
contraindicated drug is recommended), teaching models to reject falsehoods and state true contraindications.
"""

from __future__ import annotations

from typing import Any

from app.kg.store.store import DuckDBGraphStore
from app.synthetic.quality.triad import GRAQualityTriad
from app.synthetic.schema import GroundingSubgraph, ProvenancePointer, QualityMetadata, SyntheticRecord


class ContradictionRecipe:
    """Recipe for generating adversarial premise-rejection clinical training pairs."""

    @property
    def name(self) -> str:
        return "contradiction"

    def generate(
        self,
        store: DuckDBGraphStore,
        triad: GRAQualityTriad,
        count: int = 10,
        **kwargs: Any,
    ) -> list[SyntheticRecord]:
        # Query contraindication assertions
        rows = store.con.execute(
            """
            SELECT id, subject_id, predicate, object_id, population
            FROM assertions
            WHERE predicate = 'CONTRAINDICATED_IN' OR negation = TRUE
            LIMIT ?
            """,
            [count],
        ).fetchall()

        if not rows:
            # Fallback to any assertions
            rows = store.con.execute("SELECT id, subject_id, predicate, object_id, population FROM assertions LIMIT ?", [count]).fetchall()

        records: list[SyntheticRecord] = []

        for r in rows:
            ast_id, subj, pred, obj, pop = r
            ast_dict = store.get_assertion_with_evidence(ast_id)
            if not ast_dict:
                continue

            pop_str = f" in {pop}" if pop else ""
            evidence_snippets = [ev["verbatim_text"] for ev in ast_dict.get("evidence", [])]
            grounding_text = "\n".join(evidence_snippets) or f"{subj} is contraindicated in {obj}{pop_str}."

            # Formulate adversarial false premise
            adversarial_question = (
                f"Why is {subj} recommended as first-line therapy for patients with {obj}{pop_str}?"
            )

            prompt = (
                f"You are evaluating an adversarial clinical question containing a FALSE medical premise.\n\n"
                f"Clinical Evidence:\n{grounding_text}\n\n"
                f"Question with false premise:\n\"{adversarial_question}\"\n\n"
                f"Instructions:\n"
                f"1. In <think> ... </think>, explicitly recognize and debunk the false premise using the evidence.\n"
                f"2. In the RESPONSE, refute the premise directly and explain the true contraindication or safety hazard.\n\n"
                f"Format:\n"
                f"<think>\n<refutation analysis>\n</think>\n"
                f"RESPONSE: <authoritative correction>"
            )

            output = triad.generator.llm_client.generate(
                prompt=prompt,
                system_prompt="You correct false medical premises with uncompromising factual accuracy.",
                temperature=0.2,
            )

            parsed = triad.generator._parse_output(
                output, default_instruction=adversarial_question
            )
            parsed.instruction = adversarial_question

            refined_cand, _ = triad.evaluate_and_refine(parsed, grounding_text)
            if refined_cand is not None:
                prov_ptrs = [
                    ProvenancePointer(
                        doc_id=ev["doc_id"],
                        page_index=ev["page_index"],
                        block_id=ev["block_id"],
                        bbox=ev.get("bbox"),
                        source_text=ev["verbatim_text"],
                    )
                    for ev in ast_dict.get("evidence", [])
                ]

                record = SyntheticRecord(
                    task_type="adversarial_rejection",
                    domain="pharmacology",
                    instruction=refined_cand.instruction,
                    reasoning_trace=refined_cand.reasoning_trace,
                    target_response=refined_cand.target_response,
                    grounding_subgraph=GroundingSubgraph(
                        nodes=[subj, obj],
                        edges=[
                            {
                                "subject": subj,
                                "predicate": pred,
                                "object": obj,
                                "population": pop,
                            }
                        ],
                    ),
                    provenance=prov_ptrs,
                    quality_metadata=QualityMetadata(complexity_hops=1),
                )
                records.append(record)

        return records
