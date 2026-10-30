"""Canonical Synthetic Record Contract and Schemas.

Defines the target data structures for instruction tuning, chain-of-thought (CoT),
and DPO preference alignment datasets with 100% provenance anchoring.
"""

from __future__ import annotations

import uuid
from typing import Any
from pydantic import BaseModel, Field


class GroundingSubgraph(BaseModel):
    """Ontological graph context grounding the synthetic record."""

    nodes: list[str] = Field(default_factory=list, description="Canonical Entity IDs (e.g. RXNORM:187832)")
    edges: list[dict[str, Any]] = Field(default_factory=list, description="Qualified assertion edge representations")


class ProvenancePointer(BaseModel):
    """Physical provenance link to the exact source DOM block or table cell."""

    doc_id: str
    page_index: int
    block_id: str
    bbox: dict[str, float] | None = None
    source_text: str


class QualityMetadata(BaseModel):
    """Quality assurance certification stamped by the 6-Gate Trust Engine."""

    symbolic_execution_valid: bool = True
    evidence_entailment_score: float = 1.0
    ragas_faithfulness_score: float = 1.0
    leakage_check_passed: bool = True
    phi_free: bool = True
    complexity_hops: int = 1
    dedup_passed: bool = True
    provenance_audit_passed: bool = True


class SyntheticRecord(BaseModel):
    """Canonical Synthetic Record Contract.

    Curated, provenance-anchored, multi-hop reasoning & instruction artifact.
    """

    record_id: str = Field(default_factory=lambda: f"syn-rec-{uuid.uuid4().hex[:12]}")
    dataset_version: str = "v1.0.0"
    task_type: str = "multi_hop_reasoning"
    domain: str = "pharmacology"
    instruction: str
    reasoning_trace: str  # <think> step-by-step reasoning </think>
    target_response: str  # Gold chosen response
    negative_response: str | None = None  # Calibrated counterfactual for DPO
    grounding_subgraph: GroundingSubgraph
    provenance: list[ProvenancePointer] = Field(default_factory=list)
    quality_metadata: QualityMetadata = Field(default_factory=QualityMetadata)
