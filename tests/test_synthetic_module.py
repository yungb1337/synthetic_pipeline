"""Comprehensive Hermetic Unit Tests for Module #6 (app.synthetic).

Verifies canonical schema, LLM seam, dual-route router, topology sampler,
GRA Quality Triad, pluggable recipes, 6-Gate Trust Engine, and exporters.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.kg.models import Assertion, Entity, EntityMention, Evidence, HopStep, SubgraphPath
from app.kg.store.store import DuckDBGraphStore
from app.parser.dom.models import BBox, Block
from app.synthetic.cli import build_parser, run_generate
from app.synthetic.exporter.alpaca import AlpacaExporter
from app.synthetic.exporter.chatml import ChatMLExporter
from app.synthetic.exporter.dpo_arrow import DPOArrowExporter
from app.synthetic.exporter.huggingface import HuggingFaceExporter
from app.synthetic.exporter.protocol import ExporterRegistry
from app.synthetic.llm.api_client import APIClient
from app.synthetic.llm.llama_cpp import LlamaCppClient
from app.synthetic.llm.mock import MockLLMClient
from app.synthetic.llm.protocol import parse_structured_output, structured_predict_helper
from app.synthetic.quality.adjudicator_agent import AdjudicatorAgent
from app.synthetic.quality.generator_agent import CandidateGeneration, GeneratorAgent
from app.synthetic.quality.reviewer_agent import ReviewAssessment, ReviewerAgent
from app.synthetic.quality.triad import GRAQualityTriad
from app.synthetic.recipes.contradiction import ContradictionRecipe
from app.synthetic.recipes.dom_direct import DOMDirectRecipe
from app.synthetic.recipes.dpo_perturbation import DPOPerturbationRecipe
from app.synthetic.recipes.multi_hop import MultiHopRecipe
from app.synthetic.recipes.registry import RecipeRegistry
from app.synthetic.recipes.table_reasoning import TableReasoningRecipe
from app.synthetic.router import DualRouteRouter, GenerationRoute
from app.synthetic.sampler import SubgraphTopologySampler
from app.synthetic.schema import (
    GroundingSubgraph,
    ProvenancePointer,
    QualityMetadata,
    SyntheticRecord,
)
from app.synthetic.trust.consistency import GraphConsistencyGate
from app.synthetic.trust.dedup import DeDuplicationGate, compute_simhash, hamming_distance
from app.synthetic.trust.engine import TrustVerificationEngine
from app.synthetic.trust.entailment import EvidenceEntailmentGate
from app.synthetic.trust.faithfulness import FaithfulnessGate
from app.synthetic.trust.privacy import PrivacyScrubGate
from app.synthetic.trust.protocol import GateResult, TrustGate
from app.synthetic.trust.provenance import ProvenanceAuditGate


class TestSyntheticSchema:
    def test_canonical_synthetic_record_validation(self):
        rec = SyntheticRecord(
            task_type="multi_hop_reasoning",
            domain="pharmacology",
            instruction="What dosage adjustments apply to Pregabalin in CKD Stage 4?",
            reasoning_trace="<think>\n1. Patient has CKD Stage 4.\n2. Pregabalin requires dose ceiling of 25 mg.\n</think>",
            target_response="The recommended starting dose of Pregabalin in CKD Stage 4 is 25 mg once daily.",
            grounding_subgraph=GroundingSubgraph(
                nodes=["RXNORM:187832", "MESH:D051436"],
                edges=[{"subject": "RXNORM:187832", "predicate": "REQUIRES_DOSE_ADJUSTMENT", "object": "MESH:D051436"}],
            ),
            provenance=[
                ProvenancePointer(
                    doc_id="doc-1",
                    page_index=1,
                    block_id="blk-5",
                    bbox={"x0": 10.0, "y0": 20.0, "x1": 100.0, "y1": 50.0},
                    source_text="Pregabalin requires dose adjustment in CKD.",
                )
            ],
            quality_metadata=QualityMetadata(complexity_hops=2),
        )
        assert rec.record_id.startswith("syn-rec-")
        data = rec.model_dump()
        restored = SyntheticRecord.model_validate(data)
        assert restored.record_id == rec.record_id
        assert restored.quality_metadata.complexity_hops == 2


class TestLLMSeam:
    def test_mock_llm_client(self):
        client = MockLLMClient()
        gen = client.generate("Analyze drug interactions.")
        assert "<think>" in gen
        assert "</think>" in gen

        review = client.structured_predict("Review case", ReviewAssessment)
        assert review.verdict == "ACCEPT"
        assert "factual_grounding" in review.scores

    def test_llama_cpp_client_initialization_and_gpu_lock(self):
        client = LlamaCppClient(model_path=None)
        assert hasattr(LlamaCppClient, "_GPU_LOCK")
        assert client.model_name.startswith("phi4-mini-gguf:")

    def test_api_client_key_rotation(self):
        client = APIClient(
            provider="gemini",
            api_keys=["key_alpha", "key_beta"],
        )
        assert client._get_current_key() == "key_alpha"
        client._rotate_key()
        assert client._get_current_key() == "key_beta"
        client._rotate_key()
        assert client._get_current_key() == "key_alpha"

    def test_structured_predict_helper_decode_error(self):
        with pytest.raises(ValueError, match="Failed to parse LLM structured output as valid JSON"):
            parse_structured_output("Invalid not JSON at all", ReviewAssessment)


class TestDualRouteRouter:
    def test_routing_table_block(self):
        tbl_block = Block(id="b-tbl", kind="table", text="Header: Col1, Col2")
        route = DualRouteRouter.route_block(tbl_block, mentions=[])
        assert route == GenerationRoute.KG_MULTI_HOP

    def test_routing_dense_mentions(self):
        para_block = Block(id="b-para", kind="paragraph", text="Some clinical text.")
        mentions = [
            EntityMention(text="m1", category="Drug"),
            EntityMention(text="m2", category="Condition"),
            EntityMention(text="m3", category="LabTest"),
        ]
        route = DualRouteRouter.route_block(para_block, mentions)
        assert route == GenerationRoute.KG_MULTI_HOP

    def test_routing_single_mention(self):
        para_block = Block(id="b-para", kind="paragraph", text="Pregabalin text.")
        mentions = [EntityMention(text="Pregabalin", category="Drug")]
        route = DualRouteRouter.route_block(para_block, mentions)
        assert route == GenerationRoute.KG_SINGLE_HOP

    def test_routing_zero_mentions_dom_direct(self):
        para_block = Block(id="b-para", kind="paragraph", text="Narrative text with no entities.")
        route = DualRouteRouter.route_block(para_block, mentions=[])
        assert route == GenerationRoute.DOM_DIRECT


class TestGRAQualityTriad:
    def test_triad_accept_flow(self):
        llm = MockLLMClient()
        triad = GRAQualityTriad(llm_client=llm)

        cand = CandidateGeneration(
            instruction="Case question?",
            reasoning_trace="<think>\nReasoning step\n</think>",
            target_response="Final answer.",
        )
        passed_cand, assessment = triad.evaluate_and_refine(cand, "Clinical evidence context.")
        assert passed_cand is not None
        assert assessment.verdict == "ACCEPT"

    def test_triad_refinement_flow(self):
        # Configure reviewer mock to return REVISE on first pass then ACCEPT
        llm = MockLLMClient()
        reviewer_mock = ReviewerAgent(llm)
        call_count = 0

        def custom_review(cand, ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ReviewAssessment(
                    scores={"factual_grounding": 0.70},
                    verdict="REVISE",
                    critique="Include specific renal dosage ceiling.",
                )
            return ReviewAssessment(
                scores={"factual_grounding": 0.95},
                verdict="ACCEPT",
                critique="Refined answer meets all criteria.",
            )

        reviewer_mock.review = custom_review  # type: ignore
        triad = GRAQualityTriad(llm_client=llm, reviewer=reviewer_mock)

        cand = CandidateGeneration(
            instruction="Question?",
            reasoning_trace="<think>\nInitial\n</think>",
            target_response="Initial answer.",
        )
        passed_cand, assessment = triad.evaluate_and_refine(cand, "Context")
        assert passed_cand is not None
        assert assessment.verdict == "ACCEPT"
        assert call_count == 2


class TestSynthesisRecipes:
    @pytest.fixture
    def populated_store(self):
        store = DuckDBGraphStore(db_path=":memory:")
        for n in ["DrugA", "CondB", "CondC"]:
            store.insert_entity(Entity(id=n, name=n, category="Node"))
        ast1 = Assertion(id="a1", subject_id="DrugA", predicate="STEP", object_id="CondB", dosage="25 mg")
        ast1.evidence.append(
            Evidence(assertion_id="a1", doc_id="d1", page_index=0, block_id="b1", verbatim_text="DrugA step CondB")
        )
        ast2 = Assertion(id="a2", subject_id="CondB", predicate="STEP", object_id="CondC")
        ast2.evidence.append(
            Evidence(assertion_id="a2", doc_id="d1", page_index=0, block_id="b2", verbatim_text="CondB step CondC")
        )
        store.insert_assertion(ast1)
        store.insert_assertion(ast2)
        return store

    def test_multi_hop_recipe(self, populated_store):
        llm = MockLLMClient()
        triad = GRAQualityTriad(llm_client=llm)
        recipe = MultiHopRecipe()
        records = recipe.generate(store=populated_store, triad=triad, count=1, min_hops=2, max_hops=2)
        assert len(records) == 1
        assert records[0].task_type == "multi_hop_reasoning"
        assert len(records[0].provenance) == 2

    def test_dpo_perturbation_recipe(self, populated_store):
        llm = MockLLMClient()
        triad = GRAQualityTriad(llm_client=llm)
        recipe = DPOPerturbationRecipe()
        records = recipe.generate(store=populated_store, triad=triad, count=1)
        assert len(records) == 1
        assert records[0].task_type == "dpo_preference"
        assert records[0].negative_response is not None

    def test_dom_direct_recipe(self, populated_store):
        llm = MockLLMClient()
        triad = GRAQualityTriad(llm_client=llm)
        recipe = DOMDirectRecipe()
        records = recipe.generate(store=populated_store, triad=triad, count=1)
        assert len(records) == 1
        assert records[0].task_type == "factoid_qa"

    def test_sampler_dynamic_seed_limit(self, populated_store):
        sampler = SubgraphTopologySampler(populated_store)
        chains = sampler.sample_linear_chains(min_hops=2, max_hops=2, count=5, seed_limit=10)
        assert isinstance(chains, list)


class TestTrustEngine6Gates:
    @pytest.fixture
    def sample_record(self):
        return SyntheticRecord(
            instruction="What is the starting dose of Pregabalin in CKD Stage 4?",
            reasoning_trace="<think>\nPatient has eGFR 20. Pregabalin dose is 25 mg.\n</think>",
            target_response="The recommended dose of Pregabalin is 25 mg daily in severe renal impairment.",
            grounding_subgraph=GroundingSubgraph(
                nodes=["RXNORM:187832", "MESH:D051436"],
                edges=[{"subject": "RXNORM:187832", "predicate": "REQUIRES_DOSE_ADJUSTMENT", "object": "MESH:D051436"}],
            ),
            provenance=[
                ProvenancePointer(
                    doc_id="doc-1",
                    page_index=1,
                    block_id="blk-1",
                    bbox={"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0},
                    source_text="Pregabalin dose should be reduced to 25 mg daily in severe renal impairment.",
                )
            ],
        )

    def test_gate1_entailment(self, sample_record):
        gate = EvidenceEntailmentGate()
        res = gate.evaluate(sample_record)
        assert res.passed is True
        assert res.score is not None and res.score >= 0.85

    def test_gate2_consistency(self, sample_record):
        gate = GraphConsistencyGate()
        res = gate.evaluate(sample_record)
        assert res.passed is True

        # Introduce cycle
        sample_record.grounding_subgraph.edges.append(
            {"subject": "MESH:D051436", "object": "RXNORM:187832"}
        )
        res_cyclic = gate.evaluate(sample_record)
        assert res_cyclic.passed is False
        assert "Cycle detected" in res_cyclic.message

    def test_gate3_provenance_audit(self, sample_record):
        gate = ProvenanceAuditGate()
        res = gate.evaluate(sample_record)
        assert res.passed is True

        # Corrupt provenance
        sample_record.provenance[0].source_text = ""
        res_empty = gate.evaluate(sample_record)
        assert res_empty.passed is False

    def test_gate4_privacy_scrub(self, sample_record):
        gate = PrivacyScrubGate(scrub_in_place=False)
        res = gate.evaluate(sample_record)
        assert res.passed is True

        # Inject PHI (SSN)
        sample_record.target_response += " Patient SSN: 123-45-6789."
        res_phi = gate.evaluate(sample_record)
        assert res_phi.passed is False
        assert any("US_SSN" in e for e in res_phi.metadata.get("entities", []))

        # Test physician name detection
        doc_text = "Evaluated by Dr. Robert Smith MD."
        findings = gate.scan_text(doc_text)
        assert any("PHYSICIAN_NAME" in f[0] or "PERSON" in f[0] for f in findings)

    def test_gate5_faithfulness(self, sample_record):
        gate = FaithfulnessGate(threshold=0.80)
        res = gate.evaluate(sample_record)
        assert res.passed is True

        # Test hallucinated statement with non-matching clinical terms fails
        hallucinated_record = SyntheticRecord(
            instruction="What is the starting dose?",
            reasoning_trace="<think>\nNone\n</think>",
            target_response="Duloxetine is safe in severe hepatic impairment without monitoring.",
            grounding_subgraph=GroundingSubgraph(nodes=[], edges=[]),
            provenance=[
                ProvenancePointer(
                    doc_id="d1",
                    page_index=0,
                    block_id="b1",
                    source_text="Metformin is indicated for glycemic control in adult patients.",
                )
            ],
        )
        res_hallucinated = gate.evaluate(hallucinated_record)
        assert res_hallucinated.passed is False

    def test_gate6_deduplication(self, sample_record):
        gate = DeDuplicationGate()
        res1 = gate.evaluate(sample_record)
        assert res1.passed is True

        # Duplicate evaluation
        res2 = gate.evaluate(sample_record)
        assert res2.passed is False
        assert "duplicate detected" in res2.message.lower()

    def test_master_trust_engine(self, sample_record):
        engine = TrustVerificationEngine()
        all_passed, diag = engine.verify_record(sample_record)
        assert all_passed is True
        assert sample_record.quality_metadata.phi_free is True
        assert sample_record.quality_metadata.dedup_passed is True

    def test_trust_gates_protocol_conformance(self):
        gates = [
            EvidenceEntailmentGate(),
            GraphConsistencyGate(),
            ProvenanceAuditGate(),
            PrivacyScrubGate(),
            FaithfulnessGate(),
            DeDuplicationGate(),
        ]
        for g in gates:
            assert isinstance(g, TrustGate)


class TestExportersAndCLI:
    @pytest.fixture
    def test_records(self):
        return [
            SyntheticRecord(
                instruction="Question 1?",
                reasoning_trace="<think>\nTrace 1\n</think>",
                target_response="Answer 1.",
                negative_response="Counterfactual 1.",
                grounding_subgraph=GroundingSubgraph(nodes=["A", "B"], edges=[]),
                provenance=[
                    ProvenancePointer(doc_id="d1", page_index=0, block_id="b1", source_text="Source 1")
                ],
            )
        ]

    def test_chatml_exporter(self, test_records):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "chatml.jsonl"
            exporter = ChatMLExporter()
            res_path = exporter.export(test_records, out_file)
            assert res_path.exists()
            lines = res_path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["messages"][1]["content"] == "Question 1?"
            assert "<think>" in data["messages"][2]["content"]

    def test_alpaca_exporter(self, test_records):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "alpaca.json"
            exporter = AlpacaExporter()
            res_path = exporter.export(test_records, out_file)
            assert res_path.exists()
            data = json.loads(res_path.read_text(encoding="utf-8"))
            assert len(data) == 1
            assert data[0]["instruction"] == "Question 1?"

    def test_dpo_arrow_exporter(self, test_records):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "dpo.parquet"
            exporter = DPOArrowExporter()
            res_path = exporter.export(test_records, out_file)
            assert res_path.exists()
            assert res_path.stat().st_size > 0

    def test_huggingface_exporter(self, test_records):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "hf.parquet"
            exporter = HuggingFaceExporter()
            res_path = exporter.export(test_records, out_file)
            assert res_path.exists()
            assert res_path.stat().st_size > 0

    def test_cli_generate_dry_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = str(Path(tmpdir) / "records.jsonl")
            parser = build_parser()
            args = parser.parse_args(
                ["generate", "--mock", "--count", "1", "--format", "chatml", "--out", out_file]
            )
            ret = run_generate(args)
            assert ret == 0
            assert Path(out_file).exists()
