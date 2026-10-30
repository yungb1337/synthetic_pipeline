"""Stage 2 Structured Relation Extractor with LLM.

Extracts qualified clinical assertions from candidate blocks and entity mentions
using structured outputs (Instructor / Pydantic schema) or deterministic heuristic fallback.
"""

from __future__ import annotations

import re
from typing import Any
from pydantic import BaseModel, Field

from app.kg.extractor.protocol import ExtractorLLMClient
from app.kg.models import Assertion, EntityMention, Evidence
from app.kg.ontology.registry import OntologyRegistry
from app.parser.dom.models import Block


class RawExtractedAssertion(BaseModel):
    """Raw assertion schema extracted by LLM before canonical normalization."""

    subject_name: str = Field(description="Name or alias of the subject entity (e.g. drug)")
    predicate: str = Field(description="Clinical relation predicate (e.g. CONTRAINDICATED_IN, REQUIRES_DOSE_ADJUSTMENT)")
    object_name: str = Field(description="Name or alias of the object entity (e.g. condition, disease, lab test)")
    negation: bool = Field(default=False, description="True if assertion expresses a negative relation or prohibition")
    hedging: str = Field(default="", description="Hedging qualification if any (e.g. may, probable, possible)")
    population: str = Field(default="", description="Specific patient population or eGFR stage")
    dosage: str = Field(default="", description="Dosage constraints or ceiling if stated")
    timing: str = Field(default="", description="Timing or administration schedule")
    route: str = Field(default="", description="Route of administration (oral, iv, etc.)")
    evidence_level: str = Field(default="", description="Evidence grade if mentioned")
    verbatim_quote: str = Field(default="", description="Verbatim text quote from the block supporting this assertion")


class ExtractedAssertionList(BaseModel):
    assertions: list[RawExtractedAssertion] = Field(default_factory=list)


class StructuredRelationExtractor:
    """Stage 2 Relation Extractor mapping blocks and mentions into Qualified Assertions."""

    def __init__(
        self,
        llm_client: ExtractorLLMClient | None = None,
        ontology_domain: str = "pharmacology",
    ) -> None:
        self.llm_client = llm_client
        self.ontology_domain = ontology_domain

    def extract_assertions(
        self,
        block: Block,
        mentions: list[EntityMention],
        doc_id: str,
    ) -> list[Assertion]:
        """Extracts qualified assertions grounded in the given block."""
        text = block.text.strip()
        if not text:
            return []

        # Check if enough entities exist to extract relations
        canonical_mentions = [m for m in mentions if m.canonical_id is not None]
        if len(canonical_mentions) < 2 and len(mentions) < 2:
            return []

        onto = OntologyRegistry.get(self.ontology_domain)
        raw_assertions: list[RawExtractedAssertion] = []

        if self.llm_client is not None:
            prompt = (
                f"You are a clinical pharmacologist extracting qualified assertions from this text:\n"
                f"\"{text}\"\n\n"
                f"Available entities: {', '.join([m.text for m in mentions])}\n"
                f"Extract all clinical assertions connecting drugs to conditions, contraindications, or dose adjustments."
            )
            try:
                res = self.llm_client.structured_predict(
                    prompt=prompt,
                    response_model=ExtractedAssertionList,
                    system_prompt="You extract structured clinical assertions with exact quotes.",
                )
                raw_assertions = res.assertions
                if not raw_assertions:
                    raw_assertions = self._heuristic_extract(text, mentions)
            except Exception:
                raw_assertions = self._heuristic_extract(text, mentions)
        else:
            raw_assertions = self._heuristic_extract(text, mentions)

        # Convert raw extracted assertions to normalized Assertion objects
        results: list[Assertion] = []
        for raw in raw_assertions:
            subj_entity = onto.lookup_entity(raw.subject_name)
            obj_entity = onto.lookup_entity(raw.object_name)

            # Fall back to matched canonical mentions if direct lookup misses
            if not subj_entity:
                for m in canonical_mentions:
                    if m.text.lower() in raw.subject_name.lower():
                        subj_entity = onto.get_entity(m.canonical_id)
                        break
            if not obj_entity:
                for m in canonical_mentions:
                    if m.text.lower() in raw.object_name.lower() and (
                        not subj_entity or m.canonical_id != subj_entity.id
                    ):
                        obj_entity = onto.get_entity(m.canonical_id)
                        break

            if not subj_entity or not obj_entity:
                continue

            # Standardize predicate if needed
            predicate = raw.predicate.strip().upper()
            if predicate not in onto.get_predicates():
                if "CONTRA" in predicate or "AVOID" in predicate:
                    predicate = "CONTRAINDICATED_IN"
                elif "DOSE" in predicate or "ADJUST" in predicate:
                    predicate = "REQUIRES_DOSE_ADJUSTMENT"
                elif "FIRST" in predicate:
                    predicate = "FIRST_LINE_FOR"
                elif "INTERACT" in predicate:
                    predicate = "INTERACTS_WITH"
                else:
                    predicate = "CONTRAINDICATED_IN"

            # Create assertion
            assertion = Assertion(
                subject_id=subj_entity.id,
                predicate=predicate,
                object_id=obj_entity.id,
                negation=raw.negation,
                hedging=raw.hedging,
                population=raw.population,
                dosage=raw.dosage,
                timing=raw.timing,
                route=raw.route,
                evidence_level=raw.evidence_level,
                confidence=0.95,
            )

            # Create evidence anchored to block
            quote = raw.verbatim_quote.strip() or text
            ev = Evidence(
                assertion_id=assertion.id,
                doc_id=doc_id,
                page_index=block.page,
                block_id=block.id,
                bbox=block.bbox,
                verbatim_text=quote,
                entailment_score=1.0,
                verified_by_nli=False,
            )
            assertion.evidence.append(ev)
            results.append(assertion)

        return results

    def _heuristic_extract(
        self, text: str, mentions: list[EntityMention]
    ) -> list[RawExtractedAssertion]:
        """Deterministic heuristic relation extraction when LLM is unavailable."""
        raw_list: list[RawExtractedAssertion] = []
        onto = OntologyRegistry.get(self.ontology_domain)

        drugs = [m for m in mentions if m.category.lower() in ("drug", "medication")]
        conditions = [
            m
            for m in mentions
            if m.category.lower() in ("condition", "disease", "indication", "adverseeffect")
        ]

        if not drugs or not conditions:
            # Try to identify based on ontology lookup
            for m in mentions:
                ent = onto.lookup_entity(m.text)
                if ent:
                    if ent.category.lower() == "drug" and m not in drugs:
                        drugs.append(m)
                    elif ent.category.lower() == "condition" and m not in conditions:
                        conditions.append(m)

        text_lower = text.lower()
        is_negated = bool(
            re.search(r"\b(not recommended|contraindicated|avoid|do not|never|warning)\b", text_lower)
        )
        is_dose = bool(
            re.search(r"\b(dose adjustment|reduce|titrat|mg daily|maximum dose|starting dose)\b", text_lower)
        )

        predicate = "CONTRAINDICATED_IN" if is_negated else ("REQUIRES_DOSE_ADJUSTMENT" if is_dose else "FIRST_LINE_FOR")

        # Extract dosage text if present
        dosage_match = re.search(r"\b\d+(?:\.\d+)?\s*(?:mg|g|mcg)(?:\s+(?:daily|once daily|bid))?", text, re.I)
        dosage_str = dosage_match.group(0) if dosage_match else ""

        # Extract population snippet if present
        pop_match = re.search(r"\b(?:egfr|crcl|stage\s*\d|renal impairment|pediatric|geriatric)[^,\.;]*", text, re.I)
        pop_str = pop_match.group(0) if pop_match else ""

        for drug in drugs:
            for cond in conditions:
                raw_list.append(
                    RawExtractedAssertion(
                        subject_name=drug.text,
                        predicate=predicate,
                        object_name=cond.text,
                        negation=is_negated,
                        population=pop_str.strip(),
                        dosage=dosage_str.strip(),
                        verbatim_quote=text,
                    )
                )

        return raw_list
