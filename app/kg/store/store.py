"""DuckDB Graph Store for MedFactory AI Knowledge Graph.

Implements the normalized Option 2 schema (entities + assertions + evidence)
with recursive Common Table Expressions (CTEs) for 2-4 hop clinical reasoning walks.
The graph store serves as a rebuildable projection from content-addressed per-doc
extraction artifacts, NOT the primary system of record.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from app.kg.models import Assertion, Entity, Evidence, HopStep, SubgraphPath
from app.parser.dom.models import BBox


class DuckDBGraphStore:
    """Embedded columnar graph store backed by DuckDB."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self.con = duckdb.connect(database=db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        schema_path = Path(__file__).parent / "duckdb_schema.sql"
        if not schema_path.exists():
            # Fallback to parent dir if run from alternative path
            schema_path = Path(__file__).parent.parent / "duckdb_schema.sql"

        if schema_path.exists():
            schema_sql = schema_path.read_text(encoding="utf-8")
            self.con.execute(schema_sql)

    def insert_entity(self, entity: Entity) -> None:
        self.con.execute(
            """
            INSERT OR IGNORE INTO entities (id, name, category, aliases, atc_code, mesh_id, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                entity.id,
                entity.name,
                entity.category,
                entity.aliases,
                entity.atc_code,
                entity.mesh_id,
                entity.description,
            ],
        )

    def insert_entities(self, entities: list[Entity]) -> None:
        for e in entities:
            self.insert_entity(e)

    def insert_assertion(self, assertion: Assertion) -> None:
        self.con.execute(
            """
            INSERT OR IGNORE INTO assertions (
                id, subject_id, predicate, object_id, negation, hedging,
                population, dosage, timing, route, evidence_level, guideline_source, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                assertion.id,
                assertion.subject_id,
                assertion.predicate,
                assertion.object_id,
                assertion.negation,
                assertion.hedging,
                assertion.population,
                assertion.dosage,
                assertion.timing,
                assertion.route,
                assertion.evidence_level,
                assertion.guideline_source,
                assertion.confidence,
            ],
        )
        for ev in assertion.evidence:
            self.insert_evidence(ev)

    def insert_evidence(self, ev: Evidence) -> None:
        bbox_x0 = ev.bbox.x0 if ev.bbox else None
        bbox_y0 = ev.bbox.y0 if ev.bbox else None
        bbox_x1 = ev.bbox.x1 if ev.bbox else None
        bbox_y1 = ev.bbox.y1 if ev.bbox else None

        self.con.execute(
            """
            INSERT OR IGNORE INTO evidence (
                id, assertion_id, doc_id, page_index, block_id,
                bbox_x0, bbox_y0, bbox_x1, bbox_y1, verbatim_text,
                entailment_score, verified_by_nli
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ev.id,
                ev.assertion_id,
                ev.doc_id,
                ev.page_index,
                ev.block_id,
                bbox_x0,
                bbox_y0,
                bbox_x1,
                bbox_y1,
                ev.verbatim_text,
                ev.entailment_score,
                ev.verified_by_nli,
            ],
        )

    def find_assertions_by_entity(self, entity_id: str) -> list[Assertion]:
        """Finds all assertions where entity is either subject or object."""
        rows = self.con.execute(
            """
            SELECT id, subject_id, predicate, object_id, negation, hedging,
                   population, dosage, timing, route, evidence_level, guideline_source, confidence
            FROM assertions
            WHERE subject_id = ? OR object_id = ?
            """,
            [entity_id, entity_id],
        ).fetchall()

        results: list[Assertion] = []
        for r in rows:
            ast_id = r[0]
            ev_rows = self.con.execute(
                """
                SELECT id, assertion_id, doc_id, page_index, block_id,
                       bbox_x0, bbox_y0, bbox_x1, bbox_y1, verbatim_text,
                       entailment_score, verified_by_nli
                FROM evidence WHERE assertion_id = ?
                """,
                [ast_id],
            ).fetchall()

            ev_list: list[Evidence] = []
            for ev in ev_rows:
                bbox = BBox(x0=ev[5], y0=ev[6], x1=ev[7], y1=ev[8]) if ev[5] is not None else None
                ev_list.append(
                    Evidence(
                        id=ev[0],
                        assertion_id=ev[1],
                        doc_id=ev[2],
                        page_index=ev[3],
                        block_id=ev[4],
                        bbox=bbox,
                        verbatim_text=ev[9],
                        entailment_score=ev[10],
                        verified_by_nli=ev[11],
                    )
                )

            results.append(
                Assertion(
                    id=r[0],
                    subject_id=r[1],
                    predicate=r[2],
                    object_id=r[3],
                    negation=bool(r[4]),
                    hedging=r[5] or "",
                    population=r[6] or "",
                    dosage=r[7] or "",
                    timing=r[8] or "",
                    route=r[9] or "",
                    evidence_level=r[10] or "",
                    guideline_source=r[11] or "",
                    confidence=float(r[12]),
                    evidence=ev_list,
                )
            )

        return results

    def walk_paths(
        self,
        start_entity_id: str,
        min_hops: int = 2,
        max_hops: int = 4,
        enforce_same_population: bool = True,
    ) -> list[SubgraphPath]:
        """Executes a recursive CTE in DuckDB to find 2-4 hop reasoning paths."""
        pop_filter = (
            "AND (p.population = '' OR a2.population = '' OR p.population = a2.population)"
            if enforce_same_population
            else ""
        )

        query = f"""
        WITH RECURSIVE path AS (
            -- Base case: 1-hop assertions starting from seed entity
            SELECT
                a.subject_id AS root_id,
                a.subject_id,
                a.predicate,
                a.object_id,
                a.negation,
                a.population,
                a.dosage,
                1 AS hops,
                ARRAY[a.id] AS path_ids,
                ARRAY[a.subject_id, a.object_id] AS node_path
            FROM assertions a
            WHERE a.subject_id = ?

            UNION ALL

            -- Recursive step: expand outward along object -> subject
            SELECT
                p.root_id,
                a2.subject_id,
                a2.predicate,
                a2.object_id,
                a2.negation,
                COALESCE(NULLIF(p.population, ''), a2.population) AS population,
                a2.dosage,
                p.hops + 1 AS hops,
                array_append(p.path_ids, a2.id) AS path_ids,
                array_append(p.node_path, a2.object_id) AS node_path
            FROM path p
            JOIN assertions a2 ON p.object_id = a2.subject_id
            WHERE p.hops < ?
              -- Cycle prevention: do not revisit any assertion or target node
              AND NOT list_contains(p.path_ids, a2.id)
              AND NOT list_contains(p.node_path, a2.object_id)
              {pop_filter}
        )
        SELECT root_id, object_id, hops, path_ids
        FROM path
        WHERE hops BETWEEN ? AND ?;
        """

        cursor = self.con.execute(query, [start_entity_id, max_hops, min_hops, max_hops])
        results = cursor.fetchall()

        paths: list[SubgraphPath] = []
        if not results:
            return paths

        all_ast_ids = list({ast_id for row in results for ast_id in row[3]})
        ast_map: dict[str, HopStep] = {}
        if all_ast_ids:
            placeholders = ", ".join(["?"] * len(all_ast_ids))
            batch_query = f"SELECT id, subject_id, predicate, object_id, negation, population, dosage FROM assertions WHERE id IN ({placeholders})"
            for ast_row in self.con.execute(batch_query, all_ast_ids).fetchall():
                ast_id, subj_id, pred, obj_id, neg, pop, dos = ast_row
                ast_map[ast_id] = HopStep(
                    subject_id=subj_id,
                    predicate=pred,
                    object_id=obj_id,
                    negation=bool(neg),
                    population=pop or "",
                    dosage=dos or "",
                    assertion_id=ast_id,
                )

        for row in results:
            root_id, target_id, hops, path_ids = row
            steps = [ast_map[ast_id] for ast_id in path_ids if ast_id in ast_map]
            paths.append(
                SubgraphPath(
                    start_entity=root_id,
                    target_entity=target_id,
                    hops=hops,
                    steps=steps,
                    assertion_ids=path_ids,
                )
            )

        return paths

    def get_assertion_with_evidence(self, assertion_id: str) -> dict[str, Any] | None:
        ast_row = self.con.execute(
            """
            SELECT id, subject_id, predicate, object_id, negation, hedging,
                   population, dosage, timing, route, evidence_level, guideline_source, confidence
            FROM assertions WHERE id = ?
            """,
            [assertion_id],
        ).fetchone()

        if not ast_row:
            return None

        ev_rows = self.con.execute(
            """
            SELECT id, doc_id, page_index, block_id, bbox_x0, bbox_y0, bbox_x1, bbox_y1,
                   verbatim_text, entailment_score, verified_by_nli
            FROM evidence WHERE assertion_id = ?
            """,
            [assertion_id],
        ).fetchall()

        evidence_list = []
        for ev in ev_rows:
            evidence_list.append(
                {
                    "id": ev[0],
                    "doc_id": ev[1],
                    "page_index": ev[2],
                    "block_id": ev[3],
                    "bbox": {"x0": ev[4], "y0": ev[5], "x1": ev[6], "y1": ev[7]} if ev[4] is not None else None,
                    "verbatim_text": ev[8],
                    "entailment_score": ev[9],
                    "verified_by_nli": ev[10],
                }
            )

        return {
            "id": ast_row[0],
            "subject_id": ast_row[1],
            "predicate": ast_row[2],
            "object_id": ast_row[3],
            "negation": ast_row[4],
            "hedging": ast_row[5],
            "population": ast_row[6],
            "dosage": ast_row[7],
            "timing": ast_row[8],
            "route": ast_row[9],
            "evidence_level": ast_row[10],
            "guideline_source": ast_row[11],
            "confidence": ast_row[12],
            "evidence": evidence_list,
        }

    def close(self) -> None:
        self.con.close()
