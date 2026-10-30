"""Knowledge Graph Construction Module (#5).

Extracts, links, persists, and validates Qualified Clinical Assertions
anchored to Canonical DOM blocks with physical bounding box provenance.
"""

from app.kg.extractor.coreference import SimpleCoreferenceResolver
from app.kg.extractor.mentions import GLiNEREntityExtractor
from app.kg.extractor.protocol import EntityExtractor, RelationExtractor, TableExtractor
from app.kg.extractor.relations import (
    ExtractedAssertionList,
    RawExtractedAssertion,
    StructuredRelationExtractor,
)
from app.kg.extractor.tables import TableRelationalExtractor
from app.kg.models import Assertion, Entity, EntityMention, Evidence, HopStep, SubgraphPath
from app.kg.ontology.pharmacology import PharmacologyOntologyProvider
from app.kg.ontology.protocol import OntologyProvider
from app.kg.ontology.registry import OntologyRegistry
from app.kg.store.protocol import GraphStore
from app.kg.store.serializer import GraphSerializer
from app.kg.store.store import DuckDBGraphStore
from app.kg.validation import GraphValidator

__all__ = [
    "Assertion",
    "Entity",
    "EntityMention",
    "Evidence",
    "HopStep",
    "SubgraphPath",
    "DuckDBGraphStore",
    "GraphStore",
    "GraphSerializer",
    "OntologyRegistry",
    "OntologyProvider",
    "PharmacologyOntologyProvider",
    "EntityExtractor",
    "RelationExtractor",
    "TableExtractor",
    "GLiNEREntityExtractor",
    "StructuredRelationExtractor",
    "RawExtractedAssertion",
    "ExtractedAssertionList",
    "TableRelationalExtractor",
    "SimpleCoreferenceResolver",
    "GraphValidator",
]
