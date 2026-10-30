"""Synthesis Recipe Protocol.

Defines the pluggable interface for task-specific synthetic data generation strategies.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.kg.store.store import DuckDBGraphStore
from app.synthetic.quality.triad import GRAQualityTriad
from app.synthetic.schema import SyntheticRecord


@runtime_checkable
class SynthesisRecipe(Protocol):
    """Protocol for synthetic data generation strategies."""

    @property
    def name(self) -> str:
        """Unique recipe identifier (e.g. 'multi_hop', 'dpo_perturbation')."""
        ...

    def generate(
        self,
        store: DuckDBGraphStore,
        triad: GRAQualityTriad,
        count: int = 10,
        **kwargs: Any,
    ) -> list[SyntheticRecord]:
        """Generates a batch of verified SyntheticRecord artifacts."""
        ...
