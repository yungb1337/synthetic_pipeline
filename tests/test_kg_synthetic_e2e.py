"""End-to-End Pipeline Smoke Test.

Validates the full document-to-synthetic lifecycle:
Canonical DOM -> Stage 1 NER -> Stage 2 Relations -> Table Extraction ->
DuckDB Storage & Rebuildable Projection -> Subgraph Sampling ->
GRA Quality Triad -> 6-Gate Trust Certification -> Multi-format Exports.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.kg.extractor.mentions import GLiNEREntityExtractor
from app.kg.extractor.relations import StructuredRelationExtractor
from app.kg.extractor.tables import TableRelationalExtractor
from app.kg.models import Assertion, Evidence
from app.kg.ontology.registry import OntologyRegistry
from app.kg.store.serializer import GraphSerializer
from app.kg.store.store import DuckDBGraphStore
from app.parser.dom.models import BBox, Block, Cell, Document, Page, Row, Table
from app.synthetic.exporter.protocol import ExporterRegistry
from app.synthetic.llm.mock import MockLLMClient
from app.synthetic.quality.triad import GRAQualityTriad
from app.synthetic.recipes.multi_hop import MultiHopRecipe
from app.synthetic.trust.engine import TrustVerificationEngine


def test_end_to_end_synthesis_pipeline():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_p = Path(tmpdir)
        graphs_dir = tmp_p / "graphs"
        export_dir = tmp_p / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)

        doc_id = "clinical-guideline-ckd-2026"

        # 0. Construct Canonical DOM Document with paragraph and relational table
        p1 = Block(
            id="blk-p1",
            text="Metformin is contraindicated in severe renal impairment (eGFR < 30 mL/min) due to risk of lactic acidosis.",
            page=0,
            bbox=BBox(x0=50, y0=50, x1=400, y1=80),
        )
        t1 = Table(
            id="tbl-dosing",
            page=0,
            bbox=BBox(x0=50, y0=100, x1=500, y1=250),
            header=["Medication", "Condition Context", "Starting Dose"],
            rows=[
                Row(
                    cells=[
                        Cell(text="Pregabalin", bbox=BBox(x0=50, y0=120, x1=150, y1=140)),
                        Cell(text="CKD", bbox=BBox(x0=160, y0=120, x1=300, y1=140)),
                        Cell(text="25 mg once daily", bbox=BBox(x0=310, y0=120, x1=450, y1=140)),
                    ]
                )
            ],
            caption="Table 1: Renal dosage adjustments",
        )
        doc = Document(
            version="1.0.0",
            document_id=doc_id,
            source_hash="sha256-mock-hash",
            pages=[Page(index=0, blocks=[p1], tables=[t1])],
        )

        # 1. Stage 1 GLiNER NER & Entity Linking
        ner = GLiNEREntityExtractor()
        p1_mentions = ner.extract_entities(p1.text)
        assert any(m.canonical_id == "RXNORM:6809" for m in p1_mentions)  # Metformin

        # 2. Stage 2 Structured Relations & Table Extraction
        custom_resp = {
            "Grounding Reasoning Chain": (
                "INSTRUCTION: In severe renal impairment (CKD), why is Metformin contraindicated and how is Pregabalin adjusted?\n"
                "<think>\n"
                "1. Metformin is contraindicated in severe renal impairment (eGFR < 30 mL/min) due to risk of lactic acidosis.\n"
                "2. Pregabalin requires dose adjustment in CKD.\n"
                "</think>\n"
                "RESPONSE: Metformin is contraindicated in severe renal impairment due to the risk of lactic acidosis, and Pregabalin requires dose adjustment in CKD."
            )
        }
        mock_llm = MockLLMClient(custom_responses=custom_resp)
        rel_extractor = StructuredRelationExtractor(llm_client=mock_llm)
        p1_assertions = rel_extractor.extract_assertions(p1, p1_mentions, doc_id=doc_id)

        tbl_extractor = TableRelationalExtractor()
        tbl_assertions = tbl_extractor.extract_from_table(t1, doc_id=doc_id)

        all_assertions = p1_assertions + tbl_assertions
        assert len(all_assertions) >= 2

        # 3. Persistence into DuckDB store
        store = DuckDBGraphStore(db_path=":memory:")
        onto = OntologyRegistry.get("pharmacology")
        for ast in all_assertions:
            subj = onto.get_entity(ast.subject_id)
            obj = onto.get_entity(ast.object_id)
            if subj:
                store.insert_entity(subj)
            if obj:
                store.insert_entity(obj)
            store.insert_assertion(ast)

        # Connect a 2-hop bridge for multi-hop walk: Metformin -> CKD -> Pregabalin
        bridge_ast = Assertion(
            id="ast-bridge",
            subject_id="MESH:D051436",  # CKD
            predicate="REQUIRES_DOSE_ADJUSTMENT",
            object_id="RXNORM:187832",  # Pregabalin
            dosage="25 mg daily",
        )
        base_ev = tbl_assertions[0].evidence[0] if tbl_assertions[0].evidence else p1_assertions[0].evidence[0]
        bridge_ast.evidence.append(
            Evidence(
                id="ev-bridge-1",
                assertion_id=bridge_ast.id,
                doc_id=doc_id,
                page_index=base_ev.page_index,
                block_id=base_ev.block_id,
                bbox=base_ev.bbox,
                verbatim_text="Pregabalin requires dose adjustment in CKD.",
                entailment_score=1.0,
                verified_by_nli=True,
            )
        )
        store.insert_assertion(bridge_ast)

        # 4. Serialize to content-addressed projection
        artifacts = GraphSerializer.export_document_kg(store, doc_id, graphs_dir, version="1.0.0")
        assert artifacts["kg"].exists()
        assert artifacts["summary"].exists()
        assert artifacts["index"].exists()

        # Verify rebuildability
        fresh_store = DuckDBGraphStore(db_path=":memory:")
        restored = GraphSerializer.rebuild_store_from_artifacts(fresh_store, graphs_dir)
        assert restored >= 2

        # 5. Multi-Hop Synthesis via GRA Quality Triad
        triad = GRAQualityTriad(llm_client=mock_llm)
        recipe = MultiHopRecipe()
        raw_records = recipe.generate(store=fresh_store, triad=triad, count=1, min_hops=2, max_hops=2)
        assert len(raw_records) == 1
        record = raw_records[0]
        assert record.task_type == "multi_hop_reasoning"
        assert "<think>" in record.reasoning_trace

        # 6. Trust Engine 6-Gate Certification
        trust_engine = TrustVerificationEngine()
        certified = trust_engine.filter_batch([record])
        assert len(certified) == 1
        assert certified[0].quality_metadata.phi_free is True
        assert certified[0].quality_metadata.dedup_passed is True

        # 7. Multi-Format Exporters
        chatml_exp = ExporterRegistry.get("chatml")
        alpaca_exp = ExporterRegistry.get("alpaca")
        dpo_exp = ExporterRegistry.get("dpo")
        hf_exp = ExporterRegistry.get("parquet")

        chatml_path = chatml_exp.export(certified, export_dir / "train_chatml.jsonl")
        alpaca_path = alpaca_exp.export(certified, export_dir / "train_alpaca.json")
        dpo_path = dpo_exp.export(certified, export_dir / "train_dpo.parquet")
        hf_path = hf_exp.export(certified, export_dir / "train_hf.parquet")

        assert chatml_path.exists() and chatml_path.stat().st_size > 0
        assert alpaca_path.exists() and alpaca_path.stat().st_size > 0
        assert dpo_path.exists() and dpo_path.stat().st_size > 0
        assert hf_path.exists() and hf_path.stat().st_size > 0
