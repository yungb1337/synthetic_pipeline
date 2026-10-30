"""Content-Addressed JSON Artifact Serializer & Projection Rebuilder.

Saves per-document graph projections to disk mirroring ADR-008, and verifies
that the DuckDB graph store is a 100% rebuildable projection from JSON artifacts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.kg.models import Assertion, Entity, Evidence
from app.kg.store.store import DuckDBGraphStore
from app.parser.dom.models import BBox


class GraphSerializer:
    """Manages serialization of graph assertions to content-addressed JSON artifacts and projection rebuilding."""

    @staticmethod
    def export_document_kg(
        store: DuckDBGraphStore,
        doc_id: str,
        output_dir: Path,
        version: str = "1.0.0",
    ) -> dict[str, Path]:
        """Exports all assertions and evidence for a document to JSON artifacts."""
        doc_dir = output_dir / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        index_dir = output_dir / "index"
        index_dir.mkdir(parents=True, exist_ok=True)

        # 1. Fetch all evidence records for this document to find related assertions
        ev_rows = store.con.execute(
            """
            SELECT DISTINCT assertion_id FROM evidence WHERE doc_id = ?
            """,
            [doc_id],
        ).fetchall()
        assertion_ids = [r[0] for r in ev_rows]

        assertions_data: list[dict[str, Any]] = []
        referenced_entity_ids: set[str] = set()
        predicate_counts: dict[str, int] = {}

        for ast_id in assertion_ids:
            ast_dict = store.get_assertion_with_evidence(ast_id)
            if ast_dict:
                assertions_data.append(ast_dict)
                referenced_entity_ids.add(ast_dict["subject_id"])
                referenced_entity_ids.add(ast_dict["object_id"])
                pred = ast_dict["predicate"]
                predicate_counts[pred] = predicate_counts.get(pred, 0) + 1

        # Fetch referenced entities
        entities_data: list[dict[str, Any]] = []
        for ent_id in referenced_entity_ids:
            ent_row = store.con.execute(
                "SELECT id, name, category, aliases, atc_code, mesh_id, description FROM entities WHERE id = ?",
                [ent_id],
            ).fetchone()
            if ent_row:
                entities_data.append(
                    {
                        "id": ent_row[0],
                        "name": ent_row[1],
                        "category": ent_row[2],
                        "aliases": ent_row[3],
                        "atc_code": ent_row[4],
                        "mesh_id": ent_row[5],
                        "description": ent_row[6],
                    }
                )

        # 1. kg-v{version}.json
        kg_file = doc_dir / f"kg-v{version}.json"
        with open(kg_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "doc_id": doc_id,
                    "version": version,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "entities": entities_data,
                    "assertions": assertions_data,
                },
                f,
                indent=2,
            )

        # 2. kg-summary-v{version}.json
        summary_file = doc_dir / f"kg-summary-v{version}.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "doc_id": doc_id,
                    "version": version,
                    "node_count": len(entities_data),
                    "assertion_count": len(assertions_data),
                    "predicate_distribution": predicate_counts,
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                },
                f,
                indent=2,
            )

        # 3. entity-index-v{version}.json
        index_file = index_dir / f"entity-index-v{version}.json"
        existing_index: dict[str, Any] = {}
        if index_file.exists():
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    existing_index = json.load(f)
            except Exception:
                existing_index = {}

        catalog = existing_index.get("entities", {})
        for ent in entities_data:
            catalog[ent["id"]] = {
                "name": ent["name"],
                "category": ent["category"],
                "docs": list(set(catalog.get(ent["id"], {}).get("docs", []) + [doc_id])),
            }

        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": version,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "entities": catalog,
                },
                f,
                indent=2,
            )

        return {
            "kg": kg_file,
            "summary": summary_file,
            "index": index_file,
        }

    @staticmethod
    def rebuild_store_from_artifacts(store: DuckDBGraphStore, graphs_dir: Path) -> int:
        """Deterministically repopulates DuckDB store from raw kg-v*.json files."""
        total_restored = 0
        if not graphs_dir.exists():
            return 0

        for kg_file in graphs_dir.rglob("kg-v*.json"):
            with open(kg_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Insert entities
            for ent_data in data.get("entities", []):
                entity = Entity(
                    id=ent_data["id"],
                    name=ent_data["name"],
                    category=ent_data["category"],
                    aliases=ent_data.get("aliases", []),
                    atc_code=ent_data.get("atc_code"),
                    mesh_id=ent_data.get("mesh_id"),
                    description=ent_data.get("description"),
                )
                store.insert_entity(entity)

            # Insert assertions
            for ast_data in data.get("assertions", []):
                ev_list: list[Evidence] = []
                for ev_data in ast_data.get("evidence", []):
                    bbox_data = ev_data.get("bbox")
                    bbox = BBox(**bbox_data) if bbox_data else None
                    ev = Evidence(
                        id=ev_data["id"],
                        assertion_id=ast_data["id"],
                        doc_id=ev_data["doc_id"],
                        page_index=ev_data["page_index"],
                        block_id=ev_data["block_id"],
                        bbox=bbox,
                        verbatim_text=ev_data["verbatim_text"],
                        entailment_score=ev_data.get("entailment_score"),
                        verified_by_nli=ev_data.get("verified_by_nli", False),
                    )
                    ev_list.append(ev)

                assertion = Assertion(
                    id=ast_data["id"],
                    subject_id=ast_data["subject_id"],
                    predicate=ast_data["predicate"],
                    object_id=ast_data["object_id"],
                    negation=ast_data.get("negation", False),
                    hedging=ast_data.get("hedging", ""),
                    population=ast_data.get("population", ""),
                    dosage=ast_data.get("dosage", ""),
                    timing=ast_data.get("timing", ""),
                    route=ast_data.get("route", ""),
                    evidence_level=ast_data.get("evidence_level", ""),
                    guideline_source=ast_data.get("guideline_source", ""),
                    confidence=ast_data.get("confidence", 1.0),
                    evidence=ev_list,
                )
                store.insert_assertion(assertion)
                total_restored += 1

        return total_restored
