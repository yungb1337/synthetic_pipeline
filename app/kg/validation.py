"""Graph-Level Contradiction Detection and Validation Rules.

Validates ontological consistency, identifies conflicting clinical assertions
(e.g., contraindicated vs recommended), checks population compatibility,
and audits referential integrity in the graph store.
"""

from __future__ import annotations

from typing import Any

from app.kg.models import Assertion, SubgraphPath
from app.kg.store.store import DuckDBGraphStore

OPPOSING_PREDICATES = {
    ("FIRST_LINE_FOR", "CONTRAINDICATED_IN"),
    ("CONTRAINDICATED_IN", "FIRST_LINE_FOR"),
}


class GraphValidator:
    """Validates clinical graph consistency and identifies domain contradictions."""

    @staticmethod
    def find_contradictions(assertions: list[Assertion]) -> list[dict[str, Any]]:
        """Identifies conflicting assertions between identical subject and object entities."""
        contradictions: list[dict[str, Any]] = []

        # Index by (subject_id, object_id)
        pair_map: dict[tuple[str, str], list[Assertion]] = {}
        for ast in assertions:
            key = (ast.subject_id, ast.object_id)
            pair_map.setdefault(key, []).append(ast)

        for (subj, obj), group in pair_map.items():
            if len(group) < 2:
                continue

            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a1 = group[i]
                    a2 = group[j]

                    # Population overlap check: if both specify populations, check if incompatible
                    pop1 = a1.population.lower().strip()
                    pop2 = a2.population.lower().strip()
                    populations_conflict = False
                    if pop1 and pop2 and pop1 != pop2:
                        # If distinctly different populations (e.g., pediatric vs geriatric), not a contradiction
                        if ("pediatric" in pop1 and "geriatric" in pop2) or ("geriatric" in pop1 and "pediatric" in pop2):
                            continue

                    # Check 1: Opposing predicates (e.g., FIRST_LINE_FOR vs CONTRAINDICATED_IN)
                    if (a1.predicate, a2.predicate) in OPPOSING_PREDICATES:
                        contradictions.append(
                            {
                                "type": "opposing_predicates",
                                "subject_id": subj,
                                "object_id": obj,
                                "assertion_1": a1.id,
                                "assertion_2": a2.id,
                                "reason": f"Conflict between '{a1.predicate}' and '{a2.predicate}'.",
                            }
                        )

                    # Check 2: Direct negation inversion on the same predicate
                    elif a1.predicate == a2.predicate and a1.negation != a2.negation:
                        contradictions.append(
                            {
                                "type": "negation_conflict",
                                "subject_id": subj,
                                "object_id": obj,
                                "assertion_1": a1.id,
                                "assertion_2": a2.id,
                                "predicate": a1.predicate,
                                "reason": f"Same predicate '{a1.predicate}' with opposing negation states.",
                            }
                        )

        return contradictions

    @staticmethod
    def validate_path(path: SubgraphPath) -> tuple[bool, str | None]:
        """Validates that a multi-hop reasoning path is cycle-free and population-compatible."""
        # 1. Cycle detection on entities
        visited_nodes: set[str] = {path.start_entity}
        for step in path.steps:
            if step.object_id in visited_nodes:
                return False, f"Cycle detected at node '{step.object_id}' in path '{path.path_id}'."
            visited_nodes.add(step.object_id)

        # 2. Population compatibility check
        populations = [s.population.lower().strip() for s in path.steps if s.population.strip()]
        if len(set(populations)) > 1:
            has_pediatric = any("pediatric" in p for p in populations)
            has_geriatric = any("geriatric" in p for p in populations)
            if has_pediatric and has_geriatric:
                return False, f"Incompatible population mix (pediatric & geriatric) in path '{path.path_id}'."

        return True, None

    @staticmethod
    def check_referential_integrity(store: DuckDBGraphStore) -> list[str]:
        """Audits foreign key integrity: checks for dangling assertions or missing evidence."""
        issues: list[str] = []

        # Check dangling subject_id
        dangling_subj = store.con.execute(
            """
            SELECT a.id, a.subject_id FROM assertions a
            LEFT JOIN entities e ON a.subject_id = e.id
            WHERE e.id IS NULL
            """
        ).fetchall()
        for r in dangling_subj:
            issues.append(f"Assertion '{r[0]}' references non-existent subject entity '{r[1]}'.")

        # Check dangling object_id
        dangling_obj = store.con.execute(
            """
            SELECT a.id, a.object_id FROM assertions a
            LEFT JOIN entities e ON a.object_id = e.id
            WHERE e.id IS NULL
            """
        ).fetchall()
        for r in dangling_obj:
            issues.append(f"Assertion '{r[0]}' references non-existent object entity '{r[1]}'.")

        # Check assertions with zero evidence
        no_evidence = store.con.execute(
            """
            SELECT a.id FROM assertions a
            LEFT JOIN evidence ev ON a.id = ev.assertion_id
            WHERE ev.id IS NULL
            """
        ).fetchall()
        for r in no_evidence:
            issues.append(f"Assertion '{r[0]}' has zero attached evidence records.")

        return issues
