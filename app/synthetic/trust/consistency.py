"""Gate 2: Graph Consistency and Cycle Validation.

Validates that the grounding subgraph in the synthetic record is topologically sound,
free of logical cycles, and adheres to population homogeneity rules.
"""

from __future__ import annotations

from typing import Any

from app.synthetic.schema import SyntheticRecord
from app.synthetic.trust.protocol import GateResult


class GraphConsistencyGate:
    """Gate 2: Enforces topological consistency and population compatibility."""

    def evaluate(self, record: SyntheticRecord, **kwargs: Any) -> GateResult:
        """Evaluates graph consistency of the grounding subgraph."""
        subgraph = record.grounding_subgraph
        edges = subgraph.edges

        if not edges:
            # Route B direct DOM records have no graph edges
            return GateResult(
                passed=True,
                score=1.0,
                message="Gate 2 PASS: No grounding graph edges to validate (direct DOM route).",
            )

        # 1. Cycle detection in edge sequence
        visited_nodes: list[str] = []
        for edge in edges:
            subj = edge.get("subject", "")
            obj = edge.get("object", "")

            if not visited_nodes:
                visited_nodes.append(subj)

            if obj in visited_nodes:
                return GateResult(
                    passed=False,
                    score=0.0,
                    message=f"Gate 2 FAIL: Cycle detected at node '{obj}' in grounding subgraph.",
                )

            visited_nodes.append(obj)

        # 2. Population homogeneity check
        populations = [
            edge.get("population", "").strip().lower()
            for edge in edges
            if edge.get("population", "").strip()
        ]
        if len(set(populations)) > 1:
            has_ped = any("pediatric" in p for p in populations)
            has_ger = any("geriatric" in p for p in populations)
            if has_ped and has_ger:
                return GateResult(
                    passed=False,
                    score=0.0,
                    message="Gate 2 FAIL: Incompatible population qualifiers (pediatric vs geriatric) conflated.",
                )

        return GateResult(
            passed=True,
            score=1.0,
            message="Gate 2 PASS: Graph consistency and cycle checks verified.",
        )
