"""Abstract Protocols for Knowledge Graph Extractors."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from app.kg.models import Assertion, EntityMention
from app.parser.dom.models import Block, Table

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class ExtractorLLMClient(Protocol):
    """Protocol for LLM client used during structured relation extraction."""

    def structured_predict(
        self,
        prompt: str,
        response_model: type[T],
        system_prompt: str | None = None,
        temperature: float = 0.1,
    ) -> T:
        """Predicts structured output conforming to response_model."""
        ...


@runtime_checkable
class EntityExtractor(Protocol):
    """Protocol for extracting entity mentions from text blocks."""

    def extract_entities(
        self, text: str, block_context: dict[str, Any] | None = None
    ) -> list[EntityMention]:
        """Extracts entity mentions from raw block text."""
        ...


@runtime_checkable
class RelationExtractor(Protocol):
    """Protocol for extracting qualified assertions from a block and its mentions."""

    def extract_assertions(
        self,
        block: Block,
        mentions: list[EntityMention],
        doc_id: str,
    ) -> list[Assertion]:
        """Extracts qualified assertions grounded in the given block."""
        ...


@runtime_checkable
class TableExtractor(Protocol):
    """Protocol for extracting qualified assertions from structured tables."""

    def extract_from_table(self, table: Table, doc_id: str) -> list[Assertion]:
        """Extracts qualified assertions directly from tabular DOM structure."""
        ...
