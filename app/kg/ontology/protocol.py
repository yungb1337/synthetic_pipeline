"""Abstract Ontology Provider Protocol for Domain Ontologies (TBox)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.kg.models import Assertion, Entity


@runtime_checkable
class OntologyProvider(Protocol):
    """Protocol for domain-specific medical ontologies and gazetteers."""

    @property
    def domain(self) -> str:
        """Domain name (e.g. 'pharmacology', 'oncology')."""
        ...

    def lookup_entity(self, text: str, category_hint: str | None = None) -> Entity | None:
        """Looks up an entity mention using exact or fuzzy matching."""
        ...

    def get_entity(self, entity_id: str) -> Entity | None:
        """Retrieves an entity by its canonical ID (e.g. 'RXNORM:187832')."""
        ...

    def search_entities(self, query: str, limit: int = 10) -> list[Entity]:
        """Searches entities matching a search query string."""
        ...

    def get_predicates(self) -> list[str]:
        """Returns standard relationship predicates supported by this ontology."""
        ...

    def validate_assertion(self, assertion: Assertion) -> tuple[bool, str | None]:
        """Validates assertion against domain ontological rules (e.g. domain/range checks)."""
        ...
