"""Comprehensive Hermetic Unit Tests for Module #5 (app.kg).

Verifies Pydantic schemas, FlashText/RapidFuzz gazetteers, two-stage extractors,
DuckDB recursive CTE graph walks, rebuildable projections, and graph validation rules.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.kg.extractor.coreference import SimpleCoreferenceResolver
from app.kg.extractor.mentions import GLiNEREntityExtractor
from app.kg.extractor.relations import (
    ExtractedAssertionList,
    RawExtractedAssertion,
    StructuredRelationExtractor,
)
from app.kg.extractor.tables import TableRelationalExtractor
from app.kg.models import Assertion, Entity, EntityMention, Evidence, HopStep, SubgraphPath
from app.kg.ontology.pharmacology import PharmacologyOntologyProvider
from app.kg.ontology.registry import OntologyRegistry
from app.kg.store.serializer import GraphSerializer
from app.kg.store.store import DuckDBGraphStore
from app.kg.validation import GraphValidator
from app.parser.dom.models import BBox, Block, Cell, Row, Table
from app.synthetic.llm.mock import MockLLMClient


class TestKGModels:
    def test_entity_creation_and_serialization(self):
        ent = Entity(
            id="RXNORM:187832",
            name="Pregabalin",
            category="Drug",
            aliases=["Lyrica", "pregabalin"],
            atc_code="N02BF02",
        )
        data = ent.model_dump()
        assert data["id"] == "RXNORM:187832"
        assert "Lyrica" in data["aliases"]
        restored = Entity.model_validate(data)
        assert restored.name == "Pregabalin"

    def test_evidence_and_assertion_with_bbox(self):
        bbox = BBox(x0=50.0, y0=100.0, x1=200.0, y1=150.0)
        ev = Evidence(
            assertion_id="ast-1",
            doc_id="doc-001",
            page_index=1,
            block_id="blk-5",
            bbox=bbox,
            verbatim_text="Pregabalin is contraindicated in severe renal impairment.",
        )
        assert ev.id.startswith("ev-")
        assert ev.bbox.x0 == 50.0

        ast = Assertion(
            subject_id="RXNORM:187832",
            predicate="CONTRAINDICATED_IN",
            object_id="MESH:D051436",
            negation=True,
            population="eGFR < 30 mL/min",
            evidence=[ev],
        )
        assert ast.id.startswith("ast-")
        assert len(ast.evidence) == 1
        assert ast.evidence[0].block_id == "blk-5"


class TestOntologyProviderAndRegistry:
    def test_pharmacology_gazetteer_lookups(self):
        provider = OntologyRegistry.get("pharmacology")
        assert provider.domain == "pharmacology"

        # 1. Exact match via FlashText
        lyrica = provider.lookup_entity("Lyrica")
        assert lyrica is not None
        assert lyrica.id == "RXNORM:187832"
        assert lyrica.name == "Pregabalin"

        # 2. Fuzzy match via RapidFuzz
        fuzzy = provider.lookup_entity("Metformine")
        assert fuzzy is not None
        assert fuzzy.id == "RXNORM:6809"
        assert fuzzy.name == "Metformin"

        # 3. Unknown entity
        assert provider.lookup_entity("completely_unknown_molecule_xyz") is None

    def test_ontology_assertion_validation(self):
        provider = OntologyRegistry.get("pharmacology")
        valid_ast = Assertion(
            subject_id="RXNORM:187832",
            predicate="CONTRAINDICATED_IN",
            object_id="MESH:D051436",
        )
        ok, err = provider.validate_assertion(valid_ast)
        assert ok is True
        assert err is None

        invalid_pred_ast = Assertion(
            subject_id="RXNORM:187832",
            predicate="INVALID_RELATION",
            object_id="MESH:D051436",
        )
        ok, err = provider.validate_assertion(invalid_pred_ast)
        assert ok is False
        assert "Unknown predicate" in str(err)


class TestExtractors:
    def test_gliner_heuristic_fallback_extraction(self):
        extractor = GLiNEREntityExtractor(ontology_domain="pharmacology")
        text = "Duloxetine is not recommended in severe renal impairment (eGFR < 30). Starting dose is 25 mg daily."
        mentions = extractor.extract_entities(text)
        assert len(mentions) >= 1
        drug_mentions = [m for m in mentions if m.text.lower() == "duloxetine"]
        assert len(drug_mentions) == 1
        assert drug_mentions[0].canonical_id == "RXNORM:284637"

    def test_structured_relation_extractor(self):
        mock_raw = ExtractedAssertionList(
            assertions=[
                RawExtractedAssertion(
                    subject_name="Pregabalin",
                    predicate="REQUIRES_DOSE_ADJUSTMENT",
                    object_name="Chronic Kidney Disease",
                    negation=False,
                    population="Stage 4 CKD",
                    dosage="25 mg daily",
                    verbatim_quote="Pregabalin dose should be reduced to 25 mg daily in CKD.",
                )
            ]
        )
        llm = MockLLMClient(custom_structured=mock_raw)
        rel_extractor = StructuredRelationExtractor(llm_client=llm)

        block = Block(
            id="blk-10",
            text="Pregabalin dose should be reduced to 25 mg daily in CKD.",
            page=2,
            bbox=BBox(x0=10, y0=20, x1=100, y1=50),
        )
        mentions = [
            EntityMention(text="Pregabalin", category="Drug", canonical_id="RXNORM:187832"),
            EntityMention(text="CKD", category="Condition", canonical_id="MESH:D051436"),
        ]

        assertions = rel_extractor.extract_assertions(block, mentions, doc_id="doc-123")
        assert len(assertions) == 1
        ast = assertions[0]
        assert ast.subject_id == "RXNORM:187832"
        assert ast.predicate == "REQUIRES_DOSE_ADJUSTMENT"
        assert ast.object_id == "MESH:D051436"
        assert len(ast.evidence) == 1
        assert ast.evidence[0].block_id == "blk-10"
        assert ast.evidence[0].bbox.x0 == 10.0

    def test_table_relational_extractor(self):
        table = Table(
            id="tbl-1",
            page=1,
            bbox=BBox(x0=0, y0=0, x1=500, y1=300),
            header=["Medication", "Indication / Condition", "Recommended Dosage"],
            rows=[
                Row(
                    cells=[
                        Cell(text="Pregabalin", bbox=BBox(x0=10, y0=20, x1=100, y1=40)),
                        Cell(text="CKD", bbox=BBox(x0=110, y0=20, x1=200, y1=40)),
                        Cell(text="25 mg once daily", bbox=BBox(x0=210, y0=20, x1=350, y1=40)),
                    ]
                )
            ],
            caption="Table 1: Renal dosage adjustments",
        )

        table_extractor = TableRelationalExtractor()
        assertions = table_extractor.extract_from_table(table, doc_id="doc-table-1")
        assert len(assertions) >= 1
        ast = assertions[0]
        assert ast.subject_id == "RXNORM:187832"
        assert ast.object_id == "MESH:D051436"
        assert ast.evidence[0].block_id.startswith("tbl-1-r0")
        assert ast.evidence[0].bbox.x0 == 110.0

    def test_coreference_resolver(self):
        resolver = SimpleCoreferenceResolver()
        b1 = Block(id="b1", text="Pregabalin is an effective anticonvulsant.")
        m1 = [EntityMention(text="Pregabalin", category="Drug", canonical_id="RXNORM:187832")]
        res1 = resolver.process_block(b1, m1)
        assert len(res1) == 1

        b2 = Block(id="b2", text="It should be administered with food.")
        m2 = []
        res2 = resolver.process_block(b2, m2)
        assert len(res2) == 1
        assert res2[0].text == "It"
        assert res2[0].canonical_id == "RXNORM:187832"


class TestDuckDBStoreAndTraversal:
    def test_insert_and_find_assertions(self):
        store = DuckDBGraphStore(db_path=":memory:")
        ent1 = Entity(id="RXNORM:1", name="DrugA", category="Drug")
        ent2 = Entity(id="MESH:1", name="CondB", category="Condition")
        store.insert_entities([ent1, ent2])

        ast = Assertion(
            id="ast-x",
            subject_id="RXNORM:1",
            predicate="CONTRAINDICATED_IN",
            object_id="MESH:1",
            negation=True,
            population="Geriatric",
        )
        store.insert_assertion(ast)

        found = store.find_assertions_by_entity("RXNORM:1")
        assert len(found) == 1
        assert found[0].id == "ast-x"
        assert found[0].population == "Geriatric"

    def test_recursive_cte_multi_hop_walk_and_cycle_prevention(self):
        store = DuckDBGraphStore(db_path=":memory:")

        # Insert 4 nodes: A -> B -> C -> D
        for n in ["A", "B", "C", "D"]:
            store.insert_entity(Entity(id=n, name=n, category="Node"))

        # Chain: A -> B, B -> C, C -> D
        store.insert_assertion(Assertion(id="a1", subject_id="A", predicate="STEP", object_id="B"))
        store.insert_assertion(Assertion(id="a2", subject_id="B", predicate="STEP", object_id="C"))
        store.insert_assertion(Assertion(id="a3", subject_id="C", predicate="STEP", object_id="D"))

        # 2-hop walk from A: should find A -> B -> C
        paths_2hop = store.walk_paths("A", min_hops=2, max_hops=2)
        assert len(paths_2hop) == 1
        assert paths_2hop[0].hops == 2
        assert paths_2hop[0].target_entity == "C"

        # 3-hop walk from A: should find A -> B -> C -> D
        paths_3hop = store.walk_paths("A", min_hops=3, max_hops=3)
        assert len(paths_3hop) == 1
        assert paths_3hop[0].target_entity == "D"

        # Add cycle: D -> A. Walk should not loop infinitely
        store.insert_assertion(Assertion(id="a4", subject_id="D", predicate="STEP", object_id="A"))
        paths_cyclic = store.walk_paths("A", min_hops=2, max_hops=4)
        # Should terminate cleanly with finite paths
        assert len(paths_cyclic) >= 1

    def test_population_homogeneity_filter(self):
        store = DuckDBGraphStore(db_path=":memory:")
        for n in ["DrugX", "CondY", "CondZ"]:
            store.insert_entity(Entity(id=n, name=n, category="Node"))

        # Incompatible populations: pediatric vs geriatric
        store.insert_assertion(
            Assertion(id="a1", subject_id="DrugX", predicate="STEP", object_id="CondY", population="Pediatric")
        )
        store.insert_assertion(
            Assertion(id="a2", subject_id="CondY", predicate="STEP", object_id="CondZ", population="Geriatric")
        )

        paths = store.walk_paths("DrugX", min_hops=2, max_hops=2, enforce_same_population=True)
        # Incompatible population path should be dropped
        assert len(paths) == 0


class TestSerializerAndValidation:
    def test_rebuildable_projection_from_json_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            store = DuckDBGraphStore(db_path=":memory:")

            ent1 = Entity(id="RXNORM:187832", name="Pregabalin", category="Drug")
            ent2 = Entity(id="MESH:D051436", name="CKD", category="Condition")
            store.insert_entities([ent1, ent2])

            ev = Evidence(
                assertion_id="ast-reb-1",
                doc_id="doc-999",
                page_index=0,
                block_id="blk-0",
                bbox=BBox(x0=1, y0=2, x1=3, y1=4),
                verbatim_text="Sample text",
            )
            ast = Assertion(
                id="ast-reb-1",
                subject_id="RXNORM:187832",
                predicate="REQUIRES_DOSE_ADJUSTMENT",
                object_id="MESH:D051436",
                dosage="25 mg",
                evidence=[ev],
            )
            store.insert_assertion(ast)

            # Export
            artifacts = GraphSerializer.export_document_kg(store, "doc-999", out_dir, version="1.0.0")
            assert artifacts["kg"].exists()

            # Wipe store & rebuild
            new_store = DuckDBGraphStore(db_path=":memory:")
            restored_count = GraphSerializer.rebuild_store_from_artifacts(new_store, out_dir)
            assert restored_count == 1

            retrieved = new_store.get_assertion_with_evidence("ast-reb-1")
            assert retrieved is not None
            assert retrieved["subject_id"] == "RXNORM:187832"
            assert retrieved["dosage"] == "25 mg"
            assert len(retrieved["evidence"]) == 1
            assert retrieved["evidence"][0]["block_id"] == "blk-0"

    def test_contradiction_detection(self):
        a1 = Assertion(
            id="a1",
            subject_id="DrugA",
            predicate="FIRST_LINE_FOR",
            object_id="CondB",
            negation=False,
        )
        a2 = Assertion(
            id="a2",
            subject_id="DrugA",
            predicate="CONTRAINDICATED_IN",
            object_id="CondB",
            negation=False,
        )
        contradictions = GraphValidator.find_contradictions([a1, a2])
        assert len(contradictions) == 1
        assert contradictions[0]["type"] == "opposing_predicates"
