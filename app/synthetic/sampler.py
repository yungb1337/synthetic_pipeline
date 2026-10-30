"""Recursive CTE Subgraph Topology Sampler.

Extracts multi-hop reasoning chains, counterfactual perturbations for DPO,
and tabular assertion clusters directly from the DuckDB graph store.
"""

from __future__ import annotations

import copy
import random
from typing import Any

from app.kg.models import HopStep, SubgraphPath
from app.kg.store.store import DuckDBGraphStore


class SubgraphTopologySampler:
    """Samples multi-hop topologies and counterfactual perturbations for training recipes."""

    def __init__(self, store: DuckDBGraphStore) -> None:
        self.store = store

    def sample_linear_chains(
        self,
        min_hops: int = 2,
        max_hops: int = 4,
        count: int = 10,
        enforce_same_population: bool = True,
        seed_limit: int | None = None,
    ) -> list[SubgraphPath]:
        """Harvests multi-hop clinical reasoning chains using DuckDB recursive CTE walks."""
        # Query distinct seed subject entities dynamically
        limit = seed_limit if seed_limit is not None else max(50, count * 5)
        seed_rows = self.store.con.execute(
            "SELECT DISTINCT subject_id FROM assertions ORDER BY RANDOM() LIMIT ?",
            [limit],
        ).fetchall()
        seed_ids = [r[0] for r in seed_rows]

        all_paths: list[SubgraphPath] = []
        random.shuffle(seed_ids)

        for seed in seed_ids:
            paths = self.store.walk_paths(
                start_entity_id=seed,
                min_hops=min_hops,
                max_hops=max_hops,
                enforce_same_population=enforce_same_population,
            )
            all_paths.extend(paths)
            if len(all_paths) >= count:
                break

        return all_paths[:count]

    def sample_counterfactual_perturbations(
        self, path: SubgraphPath
    ) -> tuple[SubgraphPath, HopStep]:
        """Generates a counterfactually perturbed copy of a path by inverting a critical edge qualifier.

        Returns (perturbed_path, perturbed_step).
        """
        if not path.steps:
            raise ValueError(f"Path '{path.path_id}' contains zero steps.")

        perturbed_path = copy.deepcopy(path)
        perturbed_path.path_id = f"{path.path_id}-counterfactual"

        # Select target step to perturb
        target_idx = random.randint(0, len(perturbed_path.steps) - 1)
        step = perturbed_path.steps[target_idx]

        # Perturb qualifier: flip negation or invert dosage
        if step.dosage:
            step.dosage = f"unrestricted / contraindicated high-dose ({step.dosage} x 4)"
        else:
            step.negation = not step.negation

        return perturbed_path, step

    def sample_table_subgraphs(
        self, table_assertion_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Clusters assertions originating from tabular structures for comparative QA."""
        clusters: list[dict[str, Any]] = []
        for ast_id in table_assertion_ids:
            ast_data = self.store.get_assertion_with_evidence(ast_id)
            if ast_data:
                clusters.append(ast_data)
        return clusters
