"""Abstract Graph Store Protocol for MedFactory AI."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.kg.models import Assertion, Entity, Evidence, SubgraphPath


@runtime_checkable
class GraphStore(Protocol):
    """Protocol defining the interface for graph persistence and path traversal."""

    def insert_entity(self, entity: Entity) -> None:
        """Inserts or replaces an entity record."""
        ...

    def insert_entities(self, entities: list[Entity]) -> None:
        """Batch inserts entities."""
        ...

    def insert_assertion(self, assertion: Assertion) -> None:
        """Inserts an assertion with associated evidence records."""
        ...

    def insert_evidence(self, ev: Evidence) -> None:
        """Inserts an evidence record."""
        ...

    def get_assertion_with_evidence(self, assertion_id: str) -> dict[str, Any] | None:
        """Retrieves assertion details along with all attached evidence blocks."""
        ...

    def find_assertions_by_entity(self, entity_id: str) -> list[Assertion]:
        """Finds all assertions where entity is either subject or object."""
        ...

    def walk_paths(
        self,
        start_entity_id: str,
        min_hops: int = 2,
        max_hops: int = 4,
        enforce_same_population: bool = True,
    ) -> list[SubgraphPath]:
        """Performs multi-hop recursive path search starting from seed entity."""
        ...

    def close(self) -> None:
        """Closes store connection."""
        ...
