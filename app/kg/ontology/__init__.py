"""Knowledge Graph Domain Ontology Package."""

from app.kg.ontology.pharmacology import PharmacologyOntologyProvider, STANDARD_PREDICATES
from app.kg.ontology.protocol import OntologyProvider
from app.kg.ontology.registry import OntologyRegistry

__all__ = [
    "OntologyProvider",
    "OntologyRegistry",
    "PharmacologyOntologyProvider",
    "STANDARD_PREDICATES",
]
