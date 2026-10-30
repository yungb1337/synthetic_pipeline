"""Knowledge Graph & Qualified Assertion Pydantic Data Models.

Defines the core ontological primitives:
- Entity: Canonical medical concept linked to RxNorm/MeSH
- Assertion: Qualified factual assertion (NOT a bare triple)
- Evidence: 1:N physical provenance anchored into Canonical DOM blocks
- SubgraphPath: Sampled multi-hop graph walk for synthetic generation
"""

from __future__ import annotations

import uuid
from pydantic import BaseModel, Field

from app.parser.dom.models import BBox

# Alias for backward compatibility
BoundingBox = BBox


class Entity(BaseModel):
    """Canonical medical entity linked to standard terminologies."""

    id: str  # e.g. 'RXNORM:187832', 'MESH:D051436'
    name: str  # 'Pregabalin'
    category: str  # 'Drug', 'Condition', 'LabTest', 'Population'
    aliases: list[str] = Field(default_factory=list)
    atc_code: str | None = None
    mesh_id: str | None = None
    description: str | None = None


class EntityMention(BaseModel):
    """Raw entity mention extracted by GLiNER or structured LLM from a DOM block."""

    text: str
    category: str
    confidence: float = 1.0
    start_char: int | None = None
    end_char: int | None = None
    canonical_id: str | None = None  # Populated after Entity Linking


class Evidence(BaseModel):
    """Physical provenance anchoring an assertion into a Canonical DOM block."""

    id: str = Field(default_factory=lambda: f"ev-{uuid.uuid4().hex[:8]}")
    assertion_id: str
    doc_id: str
    page_index: int
    block_id: str
    bbox: BBox | None = None
    verbatim_text: str
    entailment_score: float | None = None
    verified_by_nli: bool = False


class Assertion(BaseModel):
    """Qualified Assertion (Rich N-ary assertion, NOT a bare (S,P,O) triple).

    Encodes clinical context: negation, hedging, target population, dosage,
    administration route, timing, evidence level, and guideline citation.
    """

    id: str = Field(default_factory=lambda: f"ast-{uuid.uuid4().hex[:8]}")
    subject_id: str  # Canonical Entity ID (e.g., 'RXNORM:187832')
    predicate: str  # e.g., 'REQUIRES_DOSE_ADJUSTMENT', 'CONTRAINDICATED_IN'
    object_id: str  # Canonical Entity ID (e.g., 'MESH:D051436')
    negation: bool = False
    hedging: str = ""
    population: str = ""
    dosage: str = ""
    timing: str = ""
    route: str = ""
    evidence_level: str = ""
    guideline_source: str = ""
    confidence: float = 1.0
    evidence: list[Evidence] = Field(default_factory=list)


class HopStep(BaseModel):
    subject_id: str
    predicate: str
    object_id: str
    negation: bool = False
    population: str = ""
    dosage: str = ""
    assertion_id: str


class SubgraphPath(BaseModel):
    """Multi-hop path sampled from the KG for CoT verbalization."""

    path_id: str = Field(default_factory=lambda: f"path-{uuid.uuid4().hex[:8]}")
    start_entity: str
    target_entity: str
    hops: int
    steps: list[HopStep]
    assertion_ids: list[str]
