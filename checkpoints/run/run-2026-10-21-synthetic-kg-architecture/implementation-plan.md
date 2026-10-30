# Implementation Plan — run-2026-10-21-synthetic-kg-architecture
## Knowledge Graph & Ontology-Grounded Synthetic Data Generation Engine (Modules #5 & #6)

**Author:** Technical Planner · **Date:** 2026-10-30 · **Run ID:** `run-2026-10-21-synthetic-kg-architecture`  
**Prerequisites:** Gate 2 Chief Architect (`checkpoints/run/run-2026-10-21-synthetic-kg-architecture/architecture.md` — status: `ARCHITECTURE: APPROVED`)  
**Claim Labels:** **Fact | Research | Inference | Recommendation** applied throughout.

---

## PLAN: READY

---

## 0. Executive Summary & Grounding

This implementation plan translates **Architecture Gamma ("Dual-Route Adaptive with GRA Quality Triad")** into an actionable, dependency-ordered, testable engineering roadmap. It establishes two new core platform modules alongside comprehensive evaluation suites:

1. **Module #5 (`app/kg`) — Knowledge Graph Construction:**
   - **TBox Domain Ontology & Gazetteer:** Pluggable `OntologyProvider` protocol initialized with **Pharmacology** (RxNorm + MeSH) using FlashText for exact alias matching ($O(N)$ string lookup) and RapidFuzz for fuzzy alias resolution.
   - **Two-Stage Hybrid Extraction:** Stage 1 local zero-shot NER with GLiNER (`urchade/gliner_large-v2.1`, ~40ms/page) to extract candidate spans; Stage 2 targeted structured LLM extraction (`phi4-mini:3.8B` via Instructor/Pydantic) solely for dense/ambiguous spans (~20% of blocks).
   - **Relational Table Extractor:** Deterministic mapping of Canonical DOM `Table` structures (Row $\times$ Cell layout) into Qualified Assertions with exact cell bboxes.
   - **Rebuildable DuckDB Graph Store (Option 2 Normalized Schema):** Disk-backed, columnar persistence across `entities`, `assertions`, and `evidence` tables; sub-millisecond 2–4 hop path traversal using recursive Common Table Expressions (CTEs) with array-containment cycle detection and population alignment filters.
   - **Immutable Content-Addressed Artifacts:** Deterministic export to per-document `kg-v{version}.json` projections mirroring the pattern established in ADR-008.

2. **Module #6 (`app/synthetic`) — Synthetic Data Generation Engine:**
   - **Model-Agnostic LLM Seam:** Abstract `LLMClient` protocol decoupling generation logic from backends (`LlamaCppClient` for local `phi4-mini:3.8B` GGUF, `APIClient` for remote models, and `MockLLMClient` for hermetic testing).
   - **Non-Circular Dual-Route Router:** Fast layout and mention-density heuristic deciding upfront between Route A (KG Multi-Hop) and Route B (DOM Direct Chunk QA), cutting extraction compute by ~40%.
   - **Recursive CTE Topology Sampler:** Deterministic sampling of multi-hop reasoning chains (2–4 hops), tabular subgraphs, and counterfactual edge perturbations.
   - **GRA Quality Triad:** Generator, Reviewer, and Adjudicator agent peer-review loop eliminating single LLM-as-judge biases.
   - **6-Gate Trust & Verification Engine:**
     - *Gate 1: Evidence Entailment* (DeBERTa-v3 cross-encoder NLI, score $\ge 0.85$).
     - *Gate 2: Graph Consistency* (Cycle prevention, population homogeneity, constraint satisfaction).
     - *Gate 3: Provenance Audit* (Strict verification of $(\text{doc\_id}, \text{page}, \text{block\_id}, \text{bbox}, \text{verbatim\_text})$).
     - *Gate 4: PHI / PII Scrub* (Presidio analyzer + anonymizer, 0 unmasked entities).
     - *Gate 5: Ragas Faithfulness* (Verbatim alignment score $\ge 0.95$).
     - *Gate 6: De-duplication* (Self-BLEU $\le 0.35$ and 64-bit SimHash).
   - **Multi-Format Exporters:** ChatML (with `<think>` CoT traces), Alpaca, DPO Arrow preference pairs, and HuggingFace Parquet.

3. **Evaluation Suite (`eval/kg/`):**
   - Curated gold benchmark sets (`contraindications_negation.json`, `dosage_adjustments_renal.json`, `drug_drug_interactions.json`).
   - Assertion evaluator tracking linking precision, recall, F1, negation accuracy, dosage preservation, and provenance anchoring.
   - Aggregate evaluation harness reporting fidelity metrics across model iterations.

### Hardware Envelope & Execution Safety (Fact | Recommendation)
- **Local Dev Constraints:** Windows 11, 16.5 GB RAM, 4 GB VRAM (RTX 3050).
- **GPU Residency Lock:** Only ONE neural model may reside in VRAM at any time. When `phi4-mini:3.8B` Q4_K_M GGUF (~2.32 GB) is loaded, GLiNER and DeBERTa run on CPU. A module-level `threading.Lock()` serializes all GPU inferences.
- **Hermetic Testing Guardrail:** All unit and regular test suite runs (`pytest tests/ -q`) MUST pass offline without downloading multi-gigabyte model weights by leveraging `MockLLMClient`, mock extractors, and mock NLI scorers. Tests requiring live weights are explicitly gated with `@pytest.mark.skipif(...)`.

---

## 1. Architecture → Task Traceability Matrix

| Architecture Component (Gamma §5) | Specification File(s) | Implementation Plan Task(s) |
| :--- | :--- | :--- |
| **Dependencies & Environment** | `requirements-kg.txt`, `scripts/download_kg_models.py` | Task 1.1, Task 1.2 |
| **Pydantic Ontological Models** | `app/kg/models.py` | Task 2.1 |
| **Ontology Provider & Registry** | `app/kg/ontology/protocol.py`, `app/kg/ontology/registry.py` | Task 2.2 |
| **Pharmacology Gazetteer (RxNorm/MeSH)** | `app/kg/ontology/pharmacology.py`, `ontologies/` | Task 2.3 |
| **Extractor Protocols & Seams** | `app/kg/extractor/protocol.py` | Task 2.4 |
| **Stage 1 Zero-Shot NER (GLiNER)** | `app/kg/extractor/mentions.py` | Task 2.5 |
| **Stage 2 Structured Relation Extraction** | `app/kg/extractor/relations.py` | Task 2.6 |
| **Table Relational Extractor** | `app/kg/extractor/tables.py` | Task 2.7 |
| **Coreference Resolution** | `app/kg/extractor/coreference.py` | Task 2.8 |
| **DuckDB Normalized Schema (Option 2)** | `app/kg/store/duckdb_schema.sql` | Task 2.9 |
| **DuckDB Store & Recursive CTE Walks** | `app/kg/store/store.py` | Task 2.10 |
| **Content-Addressed JSON Artifacts** | `app/kg/store/serializer.py` | Task 2.11 |
| **Graph-Level Contradiction / Consistency**| `app/kg/validation.py` | Task 2.12 |
| **Module #5 Packaging** | `app/kg/__init__.py` | Task 2.13 |
| **Canonical Synthetic Record Contract** | `app/synthetic/schema.py` | Task 3.1 |
| **Model-Agnostic LLM Protocol** | `app/synthetic/llm/protocol.py` | Task 3.2 |
| **Local GGUF Backend (phi4-mini:3.8B)** | `app/synthetic/llm/llama_cpp.py` | Task 3.3 |
| **API Client Adapter (OpenAI / Gemini)** | `app/synthetic/llm/api_client.py` | Task 3.4 |
| **Hermetic Mock LLM Client** | `app/synthetic/llm/mock.py` | Task 3.5 |
| **Non-Circular Dual-Route Router** | `app/synthetic/router.py` | Task 3.6 |
| **Recursive CTE Subgraph Topology Sampler**| `app/synthetic/sampler.py` | Task 3.7 |
| **GRA Generator Agent** | `app/synthetic/quality/generator_agent.py` | Task 3.8 |
| **GRA Reviewer Agent** | `app/synthetic/quality/reviewer_agent.py` | Task 3.9 |
| **GRA Adjudicator Agent** | `app/synthetic/quality/adjudicator_agent.py` | Task 3.10 |
| **GRA Triad Orchestrator** | `app/synthetic/quality/triad.py` | Task 3.11 |
| **Synthesis Recipe Protocol & Registry** | `app/synthetic/recipes/protocol.py`, `registry.py` | Task 3.12 |
| **Linear Multi-Hop Chain Recipe** | `app/synthetic/recipes/multi_hop.py` | Task 3.13 |
| **DPO Counterfactual Perturbation Recipe** | `app/synthetic/recipes/dpo_perturbation.py` | Task 3.14 |
| **Tabular Aggregation Recipe** | `app/synthetic/recipes/table_reasoning.py` | Task 3.15 |
| **DOM Direct Chunk QA Recipe (Route B)** | `app/synthetic/recipes/dom_direct.py` | Task 3.16 |
| **Adversarial Contradiction Recipe** | `app/synthetic/recipes/contradiction.py` | Task 3.17 |
| **Gate 1: Evidence Entailment (DeBERTa)** | `app/synthetic/trust/entailment.py` | Task 3.18 |
| **Gate 2: Graph Consistency** | `app/synthetic/trust/consistency.py` | Task 3.19 |
| **Gate 3: Physical DOM Provenance Audit** | `app/synthetic/trust/provenance.py` | Task 3.20 |
| **Gate 4: PHI / PII Scrubber (Presidio)** | `app/synthetic/trust/privacy.py` | Task 3.21 |
| **Gate 5: Ragas Faithfulness Scorer** | `app/synthetic/trust/faithfulness.py` | Task 3.22 |
| **Gate 6: De-duplication (BLEU / SimHash)**| `app/synthetic/trust/dedup.py` | Task 3.23 |
| **Trust Engine Master Pipeline** | `app/synthetic/trust/engine.py` | Task 3.24 |
| **Export Format Protocol & Registry** | `app/synthetic/exporter/protocol.py` | Task 3.25 |
| **ChatML Exporter (<think> CoT)** | `app/synthetic/exporter/chatml.py` | Task 3.26 |
| **Alpaca Instruction Exporter** | `app/synthetic/exporter/alpaca.py` | Task 3.27 |
| **DPO Preference Arrow Exporter** | `app/synthetic/exporter/dpo_arrow.py` | Task 3.28 |
| **HuggingFace Parquet Exporter** | `app/synthetic/exporter/huggingface.py` | Task 3.29 |
| **Synthetic Module CLI & Packaging** | `app/synthetic/cli.py`, `app/synthetic/__init__.py` | Task 3.30 |
| **Gold Benchmark Datasets** | `eval/kg/gold_sets/*.json` | Task 4.1 |
| **Qualified Assertion Evaluator** | `eval/kg/assertion_evaluator.py` | Task 4.2 |
| **Synthetic Evaluation Suite & CLI** | `eval/kg/suite.py`, `eval/kg/metrics/` | Task 4.3 |
| **Unit & Integration Regression Suite** | `tests/test_kg_*.py`, `tests/test_synthetic_*.py` | Task 5.1 |
| **End-to-End Pipeline Smoke Test** | `tests/test_kg_synthetic_e2e.py` | Task 5.2 |

---

## 2. Dependency Graph & Phase Sequencing

The build is partitioned into 5 sequential execution phases. Inside each phase, parallel tracks can execute independently.

```
PHASE 1: Foundation & Dependencies (Tasks 1.1 - 1.2)
   │
   ▼
PHASE 2: Module #5 — Knowledge Graph Construction (app/kg/)
   ├── 2A: Ontological Core & Pydantic Schemas (Task 2.1)
   │      ├── 2B: Ontology Provider & Gazetteer (Tasks 2.2 - 2.3)
   │      ├── 2C: Two-Stage Hybrid Extractors (Tasks 2.4 - 2.8)
   │      └── 2D: DuckDB Store & Projection (Tasks 2.9 - 2.11)
   │             │
   │             └── 2E: Graph Validation & Packaging (Tasks 2.12 - 2.13)
   │
   ▼
PHASE 3: Module #6 — Synthetic Data Generation Engine (app/synthetic/)
   ├── 3A: Canonical Contracts & LLM Seam (Tasks 3.1 - 3.5)
   ├── 3B: Dual-Route Router & Subgraph Sampler (Tasks 3.6 - 3.7)
   ├── 3C: GRA Quality Triad (Tasks 3.8 - 3.11)
   ├── 3D: Pluggable Recipes (Tasks 3.12 - 3.17)
   ├── 3E: 6-Gate Trust & Verification Engine (Tasks 3.18 - 3.24)
   └── 3F: Exporters & CLI (Tasks 3.25 - 3.30)
   │
   ▼
PHASE 4: Evaluation Suites & Gold Assertion Benchmarks (eval/kg/) (Tasks 4.1 - 4.3)
   │
   ▼
PHASE 5: End-to-End Verification & Regression Gate (Tasks 5.1 - 5.2)
```

---

## 3. Detailed Task Specifications

### Track 1: Foundations, Dependencies & Model Provisioning

#### Task 1.1: Dependency Specifications & Environment Verification
- **Files:** `requirements-kg.txt`
- **Dependencies:** None. **Blocks:** All subsequent tasks.
- **Expected Behavior (Fact):**
  - Verify `requirements-kg.txt` pins required production libraries: `gliner>=1.0`, `flashtext>=2.7`, `rapidfuzz>=3.0`, `llama-cpp-python>=0.3`, `instructor>=1.0`, `duckdb>=1.0`, `presidio-analyzer>=2.2`, `presidio-anonymizer>=2.2`, `ragas>=0.2`.
  - Ensure compatibility with existing `requirements.txt` (`pydantic>=2.7`) and `requirements-gpu.txt` (`torch`, `transformers`, `sentence-transformers`).
  - Declare optional dependencies gracefully so base CI/test environments run without GPU packages installed.
- **Tests to Add / Update:** `tests/test_kg_dependencies.py` — imports key packages with try/except guards; asserts fallback availability.
- **Definition of Done:** `requirements-kg.txt` fully specified and clean; test verifies version compatibility without crashing.

#### Task 1.2: Model Download & Verification Harness
- **Files:** `scripts/download_kg_models.py`
- **Dependencies:** Task 1.1. **Blocks:** Live integration tests.
- **Expected Behavior (Fact | Research):**
  - Robust CLI script with `--model [gliner|phi4-mini|deberta|all]` and `--verify-only` options.
  - Downloads and verifies SHA256 / file size integrity for:
    1. `urchade/gliner_large-v2.1` -> `models/gliner_large-v2.1` (~1.7 GB).
    2. `unsloth/Phi-4-mini-instruct-GGUF` (Q4_K_M) -> `models/phi4-mini/Phi-4-mini-instruct-Q4_K_M.gguf` (~2.32 GB).
    3. `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` -> `models/deberta-v3-nli` (~714 MB).
  - Skips redundant downloads if destination files exist and match expected size/hash.
- **Tests to Add / Update:** `tests/test_kg_download_script.py` — runs script in `--help` and `--verify-only` mode; asserts zero crash.
- **Definition of Done:** Script completes cleanly with exit code 0; downloads models when requested and verifies checksums.

---

### Track 2: Module #5 (`app/kg`) — Knowledge Graph Construction

#### Task 2.1: Canonical Models & BBox Alignment
- **Files:** `app/kg/models.py`
- **Dependencies:** None. **Blocks:** Tasks 2.2 - 2.13, Track 3.
- **Expected Behavior (Fact):**
  - Align with `app.parser.dom.models.BBox` to avoid spatial coordinate representation divergence:
    ```python
    from app.parser.dom.models import BBox
    ```
  - Define core Pydantic models:
    - `Entity`: `id: str` (e.g. `RXNORM:187832`), `name: str`, `category: str`, `aliases: list[str]`, `atc_code: str | None`, `mesh_id: str | None`, `description: str | None`.
    - `EntityMention`: `text: str`, `category: str`, `confidence: float`, `start_char: int | None`, `end_char: int | None`, `canonical_id: str | None`.
    - `Evidence`: `id: str`, `assertion_id: str`, `doc_id: str`, `page_index: int`, `block_id: str`, `bbox: BBox | None`, `verbatim_text: str`, `entailment_score: float | None`, `verified_by_nli: bool = False`.
    - `Assertion`: `id: str`, `subject_id: str`, `predicate: str`, `object_id: str`, `negation: bool = False`, `hedging: str = ""`, `population: str = ""`, `dosage: str = ""`, `timing: str = ""`, `route: str = ""`, `evidence_level: str = ""`, `guideline_source: str = ""`, `confidence: float = 1.0`, `evidence: list[Evidence] = Field(default_factory=list)`.
    - `HopStep`: `subject_id: str`, `predicate: str`, `object_id: str`, `negation: bool`, `population: str`, `dosage: str`, `assertion_id: str`.
    - `SubgraphPath`: `path_id: str`, `start_entity: str`, `target_entity: str`, `hops: int`, `steps: list[HopStep]`, `assertion_ids: list[str]`.
- **Tests to Add / Update:** `tests/test_kg_models.py` — asserts model validation, round-trip serialization (`model_dump()`, `model_validate()`), default IDs generation, and `BBox` compatibility.
- **Definition of Done:** `app/kg/models.py` imports cleanly, type checks with `mypy` / `pyright`, and all model tests pass.

#### Task 2.2: Abstract `OntologyProvider` Protocol & Domain Registry
- **Files:** `app/kg/ontology/protocol.py`, `app/kg/ontology/registry.py`, `app/kg/ontology/__init__.py`
- **Dependencies:** Task 2.1. **Blocks:** Task 2.3, Tasks 2.5 - 2.8.
- **Expected Behavior (Fact | Recommendation):**
  - `OntologyProvider` (`@runtime_checkable` Python Protocol):
    ```python
    class OntologyProvider(Protocol):
        @property
        def domain(self) -> str: ...
        def lookup_entity(self, text: str, category_hint: str | None = None) -> Entity | None: ...
        def get_entity(self, entity_id: str) -> Entity | None: ...
        def search_entities(self, query: str, limit: int = 10) -> list[Entity]: ...
        def get_predicates(self) -> list[str]: ...
        def validate_assertion(self, assertion: Assertion) -> tuple[bool, str | None]: ...
    ```
  - `OntologyRegistry`: Thread-safe singleton registry pattern:
    - `register(domain: str, provider: OntologyProvider) -> None`
    - `get(domain: str) -> OntologyProvider`
    - `list_domains() -> list[str]`
- **Tests to Add / Update:** `tests/test_kg_ontology_registry.py` — verifies protocol conformance via dummy provider, registration, domain lookup, and `KeyError` on unknown domain.
- **Definition of Done:** Protocol and registry implemented and verified with 100% test coverage.

#### Task 2.3: Pharmacology Gazetteer & Entity Linker (RxNorm + MeSH)
- **Files:** `app/kg/ontology/pharmacology.py`, `ontologies/pharma_starter_gazetteer.json`
- **Dependencies:** Tasks 2.1, 2.2. **Blocks:** Tasks 2.5 - 2.8, Track 4.
- **Expected Behavior (Fact | Research):**
  - Implement `PharmacologyOntologyProvider(OntologyProvider)`:
    - Loads curated terminology from `ontologies/pharma_starter_gazetteer.json` (seeded via existing `app/kg/ontology_seeder.py`).
    - Uses `flashtext.KeywordProcessor` for exact alias matching in $O(N)$ string length (e.g. mapping "Lyrica" or "pregabalin" -> `RXNORM:187832`).
    - Fallback: Uses `rapidfuzz.fuzz.token_sort_ratio` (similarity threshold $\ge 85$) for minor typographical variations.
    - Standard predicates: `CONTRAINDICATED_IN`, `REQUIRES_DOSE_ADJUSTMENT`, `FIRST_LINE_FOR`, `INTERACTS_WITH`, `MONITORING_REQUIRED`, `ADVERSE_EFFECT_OF`.
    - Auto-registers with `OntologyRegistry` under domain `"pharmacology"`.
- **Tests to Add / Update:** `tests/test_kg_pharmacology_ontology.py` — tests exact match ("Lyrica" -> Pregabalin), fuzzy match ("Metformine" -> Metformin), category filtering, predicate listing, and unknown entity lookup returning `None`.
- **Definition of Done:** `PharmacologyOntologyProvider` resolves all concepts in the starter gazetteer within < 1ms per lookup.

#### Task 2.4: Extraction Protocols & Seams
- **Files:** `app/kg/extractor/protocol.py`, `app/kg/extractor/__init__.py`
- **Dependencies:** Task 2.1. **Blocks:** Tasks 2.5 - 2.8.
- **Expected Behavior (Fact):**
  - Define extractor protocols:
    ```python
    class EntityExtractor(Protocol):
        def extract_entities(self, text: str, block_context: dict[str, Any] | None = None) -> list[EntityMention]: ...

    class RelationExtractor(Protocol):
        def extract_assertions(
            self,
            block: Block,
            mentions: list[EntityMention],
            doc_id: str,
        ) -> list[Assertion]: ...

    class TableExtractor(Protocol):
        def extract_from_table(self, table: Table, doc_id: str) -> list[Assertion]: ...
    ```
- **Tests to Add / Update:** `tests/test_kg_extractor_protocols.py` — asserts protocol structure using mock classes.
- **Definition of Done:** Extraction interfaces codified without coupling to specific neural backends.

#### Task 2.5: Stage 1 Zero-Shot NER Mentions with GLiNER
- **Files:** `app/kg/extractor/mentions.py`
- **Dependencies:** Tasks 2.1, 2.3, 2.4. **Blocks:** Task 2.6, Task 3.6.
- **Expected Behavior (Fact | Research):**
  - Implement `GLiNEREntityExtractor(EntityExtractor)`:
    - Entity labels: `["Drug", "Condition", "Dosage", "Population", "LabTest", "AdverseEffect"]`.
    - Model source: checks `models/gliner_large-v2.1` first; falls back to HuggingFace `urchade/gliner_large-v2.1` if local dir missing; provides a lightweight regex/heuristic `MockGLiNER` fallback if `gliner` library or weights are not installed (ensures hermetic test pass).
    - Threshold: default confidence threshold $\ge 0.50$.
    - Automatic Entity Linking: queries `OntologyRegistry.get("pharmacology").lookup_entity(mention.text)` to set `canonical_id`.
    - Executes on CPU by default (~40ms/page), respecting GPU memory constraint.
- **Tests to Add / Update:** `tests/test_kg_extractor_gliner.py` — tests extraction on clinical text snippet ("Duloxetine is not recommended in severe renal impairment (eGFR < 30)"), asserts detected mentions and linked `canonical_id`.
- **Definition of Done:** Stage 1 extractor runs hermetically and produces normalized `EntityMention` objects with canonical IDs.

#### Task 2.6: Stage 2 Structured Relation Extraction with Phi-4-mini
- **Files:** `app/kg/extractor/relations.py`
- **Dependencies:** Tasks 2.1, 2.3, 2.4, Task 3.2 (`LLMClient`). **Blocks:** Task 2.10, Task 3.6.
- **Expected Behavior (Fact | Research):**
  - Implement `StructuredRelationExtractor(RelationExtractor)`:
    - Only invoked when Stage 1 finds $\ge 2$ entities or relational cues (saving ~80% LLM compute).
    - Uses `LLMClient.structured_predict` with Pydantic output schema `ExtractedAssertionList`:
      ```python
      class RawExtractedAssertion(BaseModel):
          subject_name: str
          predicate: str
          object_name: str
          negation: bool = False
          hedging: str = ""
          population: str = ""
          dosage: str = ""
          timing: str = ""
          route: str = ""
          evidence_level: str = ""
          verbatim_quote: str

      class ExtractedAssertionList(BaseModel):
          assertions: list[RawExtractedAssertion]
      ```
    - Resolves `subject_name` and `object_name` to canonical IDs using `OntologyProvider`.
    - Populates `Evidence` record anchored to `(doc_id, block.page, block.id, block.bbox, verbatim_quote)`.
- **Tests to Add / Update:** `tests/test_kg_extractor_relations.py` — tests extraction using `MockLLMClient`, validating that raw predictions convert to fully qualified `Assertion` instances with valid `Evidence`.
- **Definition of Done:** Relation extractor maps unstructured paragraphs into qualified assertions with exact DOM block provenance.

#### Task 2.7: Relational Table Extraction (Row $\times$ Cell Layout)
- **Files:** `app/kg/extractor/tables.py`
- **Dependencies:** Tasks 2.1, 2.3, 2.4. **Blocks:** Task 2.10, Task 3.15.
- **Expected Behavior (Fact | Research):**
  - Implement `TableRelationalExtractor(TableExtractor)`:
    - Ingests `app.parser.dom.models.Table` instances.
    - Inspects `table.header` strings to identify key columns: Subject (e.g. "Drug", "Medication"), Predicate/Category (e.g. "Contraindications", "Dose Adjustment"), Qualifier (e.g. "eGFR Threshold", "Maximum Dose", "Route").
    - Iterates over `table.rows`:
      - Identifies subject entity in primary column.
      - Iterates across attribute cells to instantiate qualified assertions.
      - Attaches `Evidence` referencing `table.id`, `table.page`, and the specific cell's `bbox` (from `Cell.bbox` / `Row.bbox`).
- **Tests to Add / Update:** `tests/test_kg_extractor_tables.py` — constructs a synthetic 3-row dosage adjustment table; asserts correct extraction of subjects, predicates, dosages, and cell bboxes.
- **Definition of Done:** Relational tables extract directly into qualified assertions without loss of cell-level bounding box provenance.

#### Task 2.8: Document-Level Coreference Resolution
- **Files:** `app/kg/extractor/coreference.py`
- **Dependencies:** Task 2.1. **Blocks:** Task 2.6.
- **Expected Behavior (Fact | Research):**
  - Implement `SimpleCoreferenceResolver`:
    - Operates sequentially along `Document.reading_order`.
    - Maintains a short-term entity stack within sliding window (default 3 blocks).
    - Resolves clinical anaphoric pronouns ("this drug", "the agent", "it", "these patients") to the most recently mentioned compatible canonical entity.
    - Avoids false linkage across major heading boundaries.
- **Tests to Add / Update:** `tests/test_kg_coreference.py` — verifies that a block saying "It should be administered at 25 mg daily" following a "Pregabalin" block resolves "It" -> `RXNORM:187832`.
- **Definition of Done:** Coreference resolver replaces ambiguous mentions with canonical entity IDs across adjacent DOM blocks.

#### Task 2.9: Normalized DuckDB Schema & DDL
- **Files:** `app/kg/store/duckdb_schema.sql`
- **Dependencies:** Task 2.1. **Blocks:** Task 2.10.
- **Expected Behavior (Fact):**
  - Review and finalize `duckdb_schema.sql`:
    - `entities`: `(id VARCHAR PRIMARY KEY, name VARCHAR, category VARCHAR, aliases VARCHAR[], atc_code VARCHAR, mesh_id VARCHAR, description VARCHAR, created_at TIMESTAMP)`.
    - `assertions`: `(id VARCHAR PRIMARY KEY, subject_id VARCHAR FK, predicate VARCHAR, object_id VARCHAR FK, negation BOOLEAN, hedging VARCHAR, population VARCHAR, dosage VARCHAR, timing VARCHAR, route VARCHAR, evidence_level VARCHAR, guideline_source VARCHAR, confidence DOUBLE, created_at TIMESTAMP)`.
    - `evidence`: `(id VARCHAR PRIMARY KEY, assertion_id VARCHAR FK, doc_id VARCHAR, page_index INTEGER, block_id VARCHAR, bbox_x0 DOUBLE, bbox_y0 DOUBLE, bbox_x1 DOUBLE, bbox_y1 DOUBLE, verbatim_text VARCHAR, entailment_score DOUBLE, verified_by_nli BOOLEAN, created_at TIMESTAMP)`.
    - Performance indexes on `assertions(subject_id)`, `assertions(object_id)`, `assertions(predicate)`, and `evidence(assertion_id)`.
- **Tests to Add / Update:** `tests/test_kg_duckdb_schema.py` — executes DDL in DuckDB in-memory database; asserts tables, foreign keys, and indexes exist.
- **Definition of Done:** SQL schema executes idempotently without syntax errors.

#### Task 2.10: DuckDB Store Implementation & Recursive CTE Multi-Hop Path Query
- **Files:** `app/kg/store/store.py`
- **Dependencies:** Tasks 2.1, 2.9. **Blocks:** Task 2.11, Task 2.12, Task 3.7.
- **Expected Behavior (Fact | Research):**
  - Implement `DuckDBGraphStore`:
    - Constructor accepts `db_path: str = ":memory:"`.
    - Batch insert methods: `insert_entity(entity: Entity)`, `insert_entities(entities: list[Entity])`, `insert_assertion(assertion: Assertion)`, `insert_evidence(evidence: Evidence)`.
    - Retrieval: `get_assertion_with_evidence(assertion_id: str) -> dict | None`, `find_assertions_by_entity(entity_id: str) -> list[Assertion]`.
    - Multi-hop traversal: `walk_paths(start_entity_id: str, min_hops: int = 2, max_hops: int = 4, enforce_same_population: bool = True) -> list[SubgraphPath]`.
      - Executes recursive CTE in DuckDB.
      - Cycle detection using `NOT list_contains(p.path_ids, a2.id) AND NOT list_contains(p.node_path, a2.object_id)`.
      - Population homogeneity preservation: `(p.population = '' OR a2.population = '' OR p.population = a2.population)`.
      - Returns typed `SubgraphPath` instances with full assertion details and lineage.
- **Tests to Add / Update:** `tests/test_kg_duckdb_store.py` — inserts a 4-node chain (`A -> B -> C -> D`); verifies `walk_paths("A", min_hops=2, max_hops=3)` returns exact paths; verifies cyclic graph `A -> B -> C -> A` terminates without infinite loop; verifies population filter drops incompatible edges.
- **Definition of Done:** DuckDB store inserts and queries graph subgraphs with < 2ms latency on 10,000 assertions.

#### Task 2.11: Content-Addressed JSON Artifact Storage
- **Files:** `app/kg/store/serializer.py`
- **Dependencies:** Tasks 2.1, 2.10. **Blocks:** Task 2.13, Gate 7.
- **Expected Behavior (Fact | Recommendation):**
  - Implement `GraphSerializer`:
    - Persists per-document extraction artifacts to disk mirroring ADR-008:
      - `graphs/{doc_id}/kg-v{version}.json`: Complete Qualified Assertions + Evidence records for the document.
      - `graphs/{doc_id}/kg-summary-v{version}.json`: Node count, assertion count, predicate distribution, extraction timestamp.
      - `graphs/index/entity-index-v{version}.json`: Cross-document entity catalog.
    - Projection Rebuild: `rebuild_store_from_artifacts(store: DuckDBGraphStore, graphs_dir: Path) -> int`:
      - Deterministically repopulates DuckDB store from raw `kg-v{n}.json` files, proving the graph database is a rebuildable projection.
- **Tests to Add / Update:** `tests/test_kg_serializer.py` — serializes assertions to JSON; wipes in-memory DuckDB store; rebuilds store from files; verifies queried assertions are identical.
- **Definition of Done:** Rebuildable projection verified: DuckDB database can be deleted and reconstructed byte-identically from JSON artifacts.

#### Task 2.12: Graph-Level Contradiction Detection & Population Consistency
- **Files:** `app/kg/validation.py`
- **Dependencies:** Tasks 2.1, 2.10. **Blocks:** Task 3.19 (Gate 2).
- **Expected Behavior (Fact | Research):**
  - Implement `GraphValidator`:
    - Contradiction Detection: Identifies diametrically opposed assertions (e.g. `Assertion(A, "FIRST_LINE_FOR", B)` vs `Assertion(A, "CONTRAINDICATED_IN", B)`) under overlapping populations.
    - Population Compatibility: Ensures multi-hop reasoning chains do not bridge pediatric-only guidelines with geriatric-only dosage rules.
    - Referential Integrity: Flags dangling entity references or missing evidence pointers.
- **Tests to Add / Update:** `tests/test_kg_validation.py` — feeds intentional contradictory assertion pair; asserts `GraphValidator.find_contradictions()` detects conflict.
- **Definition of Done:** Validator identifies contradictory and invalid graph subgraphs with deterministic rule-based checks.

#### Task 2.13: Module #5 Packaging & Public Exports
- **Files:** `app/kg/__init__.py`
- **Dependencies:** Tasks 2.1 - 2.12. **Blocks:** Track 3.
- **Expected Behavior (Fact):**
  - Package exports:
    ```python
    from app.kg.models import Entity, EntityMention, Evidence, Assertion, SubgraphPath
    from app.kg.store.store import DuckDBGraphStore
    from app.kg.ontology.registry import OntologyRegistry
    from app.kg.ontology.pharmacology import PharmacologyOntologyProvider
    from app.kg.extractor.mentions import GLiNEREntityExtractor
    from app.kg.extractor.relations import StructuredRelationExtractor
    from app.kg.extractor.tables import TableRelationalExtractor
    ```
- **Tests to Add / Update:** `tests/test_kg_package.py` — verifies all top-level symbols import cleanly from `app.kg`.
- **Definition of Done:** Clean public API surface exposed for Module #5.

---

### Track 3: Module #6 (`app/synthetic`) — Synthetic Data Generation Engine

#### Task 3.1: Canonical Synthetic Record Contract
- **Files:** `app/synthetic/schema.py`
- **Dependencies:** Task 2.1. **Blocks:** Tasks 3.2 - 3.30.
- **Expected Behavior (Fact):**
  - Implement Pydantic schema for canonical synthetic artifacts:
    - `GroundingSubgraph`: `nodes: list[str]`, `edges: list[dict[str, Any]]`.
    - `ProvenancePointer`: `doc_id: str`, `page_index: int`, `block_id: str`, `bbox: dict[str, float] | None`, `source_text: str`.
    - `QualityMetadata`:
      - `symbolic_execution_valid: bool = True`
      - `evidence_entailment_score: float = 1.0`
      - `ragas_faithfulness_score: float = 1.0`
      - `phi_free: bool = True`
      - `leakage_check_passed: bool = True`
      - `complexity_hops: int = 1`
    - `SyntheticRecord`:
      - `record_id: str` (UUID with prefix `syn-rec-`)
      - `dataset_version: str = "v1.0.0"`
      - `task_type: str` (e.g. `multi_hop_reasoning`, `dpo_preference`, `table_qa`, `factoid_qa`)
      - `domain: str = "pharmacology"`
      - `instruction: str`
      - `reasoning_trace: str` (`<think> ... </think>`)
      - `target_response: str`
      - `negative_response: str | None = None`
      - `grounding_subgraph: GroundingSubgraph`
      - `provenance: list[ProvenancePointer]`
      - `quality_metadata: QualityMetadata`
- **Tests to Add / Update:** `tests/test_synthetic_schema.py` — asserts model validation, JSON round-trip, default field values, and schema invariants.
- **Definition of Done:** Canonical schema passes validation; serializes to strict JSON/Dict without errors.

#### Task 3.2: Model-Agnostic LLM Seam Protocol
- **Files:** `app/synthetic/llm/protocol.py`, `app/synthetic/llm/__init__.py`
- **Dependencies:** Task 3.1. **Blocks:** Tasks 3.3 - 3.5, Tasks 3.8 - 3.10.
- **Expected Behavior (Fact):**
  - Define `LLMClient` `@runtime_checkable` Protocol:
    ```python
    T = TypeVar("T", bound=BaseModel)

    class LLMClient(Protocol):
        @property
        def model_name(self) -> str: ...
        def generate(
            self,
            prompt: str,
            system_prompt: str | None = None,
            temperature: float = 0.2,
            max_tokens: int = 2048,
        ) -> str: ...
        def structured_predict(
            self,
            prompt: str,
            response_model: type[T],
            system_prompt: str | None = None,
            temperature: float = 0.1,
        ) -> T: ...
    ```
- **Tests to Add / Update:** `tests/test_llm_protocol.py` — validates protocol signatures using mock classes.
- **Definition of Done:** `LLMClient` protocol codified and ready for pluggable implementations.

#### Task 3.3: Local GGUF Backend via `llama-cpp-python` (phi4-mini:3.8B)
- **Files:** `app/synthetic/llm/llama_cpp.py`
- **Dependencies:** Task 3.2, Task 1.1. **Blocks:** Production synthesis runs.
- **Expected Behavior (Fact | Research):**
  - Implement `LlamaCppClient(LLMClient)`:
    - Default model path: `models/phi4-mini/Phi-4-mini-instruct-Q4_K_M.gguf`.
    - Hardware-respecting configuration: `n_ctx=4096`, `n_gpu_layers=-1` (offloads layers to RTX 3050 within 4 GB VRAM envelope).
    - GPU Concurrency Lock: Class-level `_GPU_LOCK = threading.Lock()` ensuring strict sequential GPU execution (`--heavy-concurrency 1`).
    - Pydantic structured output integration via `instructor.from_openai` or `llama-cpp-python` grammar schema mode.
    - Graceful fallback: If CUDA context fails or `llama_cpp` not installed, logs warning and falls back to CPU or raises descriptive `HardwareResourceError`.
- **Tests to Add / Update:** `tests/test_llm_llama_cpp.py` — mock-tests instantiation, parameters, and skip pattern when weights absent.
- **Definition of Done:** GGUF inference client implemented with GPU locking and structured output support.

#### Task 3.4: API Client Adapter (OpenAI / Gemini)
- **Files:** `app/synthetic/llm/api_client.py`
- **Dependencies:** Task 3.2. **Blocks:** Synthesis runs with frontier models.
- **Expected Behavior (Fact | Research):**
  - Implement `APIClient(LLMClient)`:
    - Supports Gemini (using Google GenAI API) and OpenAI-compatible endpoints.
    - Respects memory rules: sequentially rotates API keys (`key`, `key1`, `key2`), handles 429 rate limits with exponential backoff and random jitter.
    - Never echoes or logs secret API keys.
    - Uses `instructor` patch for structured outputs.
- **Tests to Add / Update:** `tests/test_llm_api_client.py` — mock HTTP responses; verifies retry logic on 429 and key rotation.
- **Definition of Done:** API adapter handles rate limits resiliently with zero secret leakage.

#### Task 3.5: Hermetic Mock LLM Client
- **Files:** `app/synthetic/llm/mock.py`
- **Dependencies:** Task 3.2. **Blocks:** Hermetic unit testing of GRA triad and recipes.
- **Expected Behavior (Fact):**
  - Implement `MockLLMClient(LLMClient)`:
    - Operates 100% offline without dependencies or model weights.
    - `generate()` returns template-driven synthetic responses containing valid `<think>` reasoning traces.
    - `structured_predict()` inspects `response_model` and returns valid schema-compliant dummy objects.
- **Tests to Add / Update:** `tests/test_llm_mock.py` — verifies generation output, structured predictions, and determinism.
- **Definition of Done:** Hermetic mock available for instant unit testing across all synthesis modules.

#### Task 3.6: Non-Circular Dual-Route Decision Router
- **Files:** `app/synthetic/router.py`
- **Dependencies:** Tasks 2.1, 2.5. **Blocks:** End-to-end synthesis pipeline.
- **Expected Behavior (Fact | Research):**
  - Implement `DualRouteRouter`:
    ```python
    class GenerationRoute(str, Enum):
        KG_MULTI_HOP = "kg_multi_hop"   # Complex: relational/table or dense entities
        KG_SINGLE_HOP = "kg_single_hop" # Structured single assertion
        DOM_DIRECT = "dom_direct"       # Narrative: simple chunk retrieval factoid QA

    def route_block(block: Block, mentions: list[EntityMention]) -> GenerationRoute:
        if block.kind == "table":
            return GenerationRoute.KG_MULTI_HOP
        if len(mentions) >= 3:
            return GenerationRoute.KG_MULTI_HOP
        if len(mentions) >= 1:
            return GenerationRoute.KG_SINGLE_HOP
        return GenerationRoute.DOM_DIRECT
    ```
  - **Non-Circular Guarantee:** Evaluates solely on `Block` metadata and Stage 1 GLiNER mentions *before* any graph insertion or LLM calls occur.
- **Tests to Add / Update:** `tests/test_synthetic_router.py` — tests table block -> `KG_MULTI_HOP`; 4 mentions -> `KG_MULTI_HOP`; 1 mention -> `KG_SINGLE_HOP`; 0 mentions -> `DOM_DIRECT`.
- **Definition of Done:** Routing logic runs deterministically in < 1ms per block without external dependencies.

#### Task 3.7: Recursive CTE Subgraph Topology Sampler
- **Files:** `app/synthetic/sampler.py`
- **Dependencies:** Tasks 2.1, 2.10. **Blocks:** Tasks 3.13, 3.14.
- **Expected Behavior (Fact | Research):**
  - Implement `SubgraphTopologySampler`:
    - `sample_linear_chains(min_hops=2, max_hops=4, count=10) -> list[SubgraphPath]`:
      - Queries `DuckDBGraphStore.walk_paths` to harvest clinical reasoning chains (e.g. *Pregabalin -> requires dose adjustment -> CKD Stage 4 -> characterized by eGFR < 30*).
    - `sample_counterfactual_perturbations(path: SubgraphPath) -> tuple[SubgraphPath, HopStep]`:
      - Selects a pivotal assertion in the path and perturbs a qualifier (e.g. flips `negation: False -> True`, or alters `dosage: "25 mg" -> "150 mg"`), creating calibrated negative pairs for DPO.
    - `sample_table_subgraphs(table_assertion_ids: list[str]) -> list[dict]`:
      - Clusters assertions originating from the same table for cross-cell comparison synthesis.
- **Tests to Add / Update:** `tests/test_synthetic_sampler.py` — populates DuckDB test store; verifies sampled linear chains match requested hop length; verifies counterfactual perturbation inverts only the targeted qualifier.
- **Definition of Done:** Topology sampler extracts multi-hop chains and counterfactuals deterministically from the graph store.

#### Task 3.8: GRA Generator Agent
- **Files:** `app/synthetic/quality/generator_agent.py`
- **Dependencies:** Tasks 3.1, 3.2. **Blocks:** Task 3.11.
- **Expected Behavior (Fact | Research):**
  - Implement `GeneratorAgent`:
    - Ingests a sampled `SubgraphPath` or DOM text chunk.
    - Formulates prompt incorporating entities, predicates, and clinical qualifiers.
    - Calls `LLMClient.generate` to synthesize:
      1. Clinical instruction / clinical scenario question.
      2. Step-by-step reasoning trace encased in `<think> ... </think>`.
      3. Precise, authoritative final answer grounded strictly in the qualifiers.
- **Tests to Add / Update:** `tests/test_gra_generator.py` — tests generation with `MockLLMClient`; verifies presence of `<think>` tags and instruction structure.
- **Definition of Done:** Generator produces candidate records from graph subgraphs.

#### Task 3.9: GRA Reviewer Agent
- **Files:** `app/synthetic/quality/reviewer_agent.py`
- **Dependencies:** Tasks 3.1, 3.2. **Blocks:** Task 3.11.
- **Expected Behavior (Fact | Research):**
  - Implement `ReviewerAgent`:
    - Acts as an independent clinical critic evaluating the candidate produced by `GeneratorAgent`.
    - Evaluates 4 explicit dimensions:
      1. *Factual Grounding:* Are all stated dosages/contraindications supported by the grounding assertions?
      2. *Reasoning Validity:* Does the `<think>` trace logically connect the premise to the conclusion without logical leaps?
      3. *Clarity & Grammar:* Is the language professional, direct, and unambiguous?
      4. *Dosage & Safety Preservation:* Did the generator hallucinate non-existent drug limits?
    - Output schema: `ReviewAssessment` containing `scores: dict[str, float]`, `verdict: Literal["ACCEPT", "REVISE", "REJECT"]`, `critique: str`.
- **Tests to Add / Update:** `tests/test_gra_reviewer.py` — tests review assessment parsing on compliant and non-compliant samples using `MockLLMClient`.
- **Definition of Done:** Reviewer evaluates candidate records and returns structured critique with explicit numeric scores.

#### Task 3.10: GRA Adjudicator Agent
- **Files:** `app/synthetic/quality/adjudicator_agent.py`
- **Dependencies:** Tasks 3.1, 3.2, 3.9. **Blocks:** Task 3.11.
- **Expected Behavior (Fact | Research):**
  - Implement `AdjudicatorAgent`:
    - Invoked when the Reviewer issues a `REVISE` verdict or when score variance is high.
    - Inspects candidate instruction, reasoning trace, reviewer critique, and raw DOM evidence text.
    - Serves as the final arbiter: either synthesizes a corrected response or issues a permanent `REJECT`.
    - Eliminates single LLM judge blind spots and majority misjudgment.
- **Tests to Add / Update:** `tests/test_gra_adjudicator.py` — tests arbitration logic when Reviewer returns `REVISE`.
- **Definition of Done:** Adjudicator resolves reviewer disputes and outputs final validated text.

#### Task 3.11: GRA Quality Triad Orchestrator
- **Files:** `app/synthetic/quality/triad.py`, `app/synthetic/quality/__init__.py`
- **Dependencies:** Tasks 3.8, 3.9, 3.10. **Blocks:** Tasks 3.13 - 3.17.
- **Expected Behavior (Fact | Research):**
  - Implement `GRAQualityTriad`:
    - Coordinates Generator -> Reviewer -> Adjudicator peer-review loop.
    - If Reviewer accepts candidate on Pass 1 (all scores $\ge 0.85$), candidate passes immediately.
    - If Reviewer requests revision, Adjudicator reviews and attempts 1 refinement round.
    - Maximum loop: 2 refinement passes before discarding sample to guarantee bounded compute.
- **Tests to Add / Update:** `tests/test_gra_triad.py` — verifies end-to-end triad pipeline with mock agents across ACCEPT, REVISE, and REJECT scenarios.
- **Definition of Done:** GRA Triad executes peer review loop and outputs quality-stamped synthetic candidates.

#### Task 3.12: Synthesis Recipe Protocol & Registry
- **Files:** `app/synthetic/recipes/protocol.py`, `app/synthetic/recipes/registry.py`, `app/synthetic/recipes/__init__.py`
- **Dependencies:** Task 3.1, Task 3.11. **Blocks:** Tasks 3.13 - 3.17.
- **Expected Behavior (Fact):**
  - Define `SynthesisRecipe` `@runtime_checkable` Protocol:
    ```python
    class SynthesisRecipe(Protocol):
        @property
        def name(self) -> str: ...
        def generate(
            self,
            store: DuckDBGraphStore,
            triad: GRAQualityTriad,
            count: int = 10,
            **kwargs: Any,
        ) -> list[SyntheticRecord]: ...
    ```
  - Implement `RecipeRegistry`:
    - `register(recipe: SynthesisRecipe) -> None`
    - `get(name: str) -> SynthesisRecipe`
    - `list_recipes() -> list[str]`
- **Tests to Add / Update:** `tests/test_synthetic_recipe_registry.py` — tests registration and discovery of synthesis recipes.
- **Definition of Done:** Recipe protocol and registry codified and tested.

#### Task 3.13: Linear Multi-Hop Chain CoT Recipe
- **Files:** `app/synthetic/recipes/multi_hop.py`
- **Dependencies:** Tasks 3.7, 3.11, 3.12. **Blocks:** Task 3.30.
- **Expected Behavior (Fact | Research):**
  - Implement `MultiHopRecipe(SynthesisRecipe)`:
    - Samples 2–4 hop linear chains using `SubgraphTopologySampler`.
    - Drives `GRAQualityTriad` to generate multi-step clinical deduction questions (e.g. *A patient with T2DM presents with eGFR 22 mL/min and neuropathic pain. Can Pregabalin and Metformin be prescribed, and what dose adjustments apply?*).
    - Assembles `SyntheticRecord` with `task_type="multi_hop_reasoning"`, full `grounding_subgraph`, and exact block `provenance`.
- **Tests to Add / Update:** `tests/test_recipe_multi_hop.py` — runs recipe with mock store and mock triad; asserts output record structure.
- **Definition of Done:** Multi-hop recipe generates grounded CoT reasoning records from graph chains.

#### Task 3.14: Counterfactual Edge Inversion DPO Recipe
- **Files:** `app/synthetic/recipes/dpo_perturbation.py`
- **Dependencies:** Tasks 3.7, 3.11, 3.12. **Blocks:** Task 3.28, Task 3.30.
- **Expected Behavior (Fact | Research):**
  - Implement `DPOPerturbationRecipe(SynthesisRecipe)`:
    - Generates chosen (gold) response from valid multi-hop path.
    - Generates rejected (counterfactual) response by inverting a critical edge qualifier (e.g. asserting drug is safe at full dose despite eGFR < 30).
    - Populates `target_response` (chosen) and `negative_response` (rejected) in `SyntheticRecord`.
- **Tests to Add / Update:** `tests/test_recipe_dpo.py` — verifies chosen/rejected pair generation and counterfactual tag attachment.
- **Definition of Done:** DPO recipe outputs calibrated preference pairs for alignment training.

#### Task 3.15: Tabular Aggregation & Reasoning Recipe
- **Files:** `app/synthetic/recipes/table_reasoning.py`
- **Dependencies:** Tasks 2.7, 3.11, 3.12. **Blocks:** Task 3.30.
- **Expected Behavior (Fact | Research):**
  - Implement `TableReasoningRecipe(SynthesisRecipe)`:
    - Ingests relational assertions derived from DOM `Table` structures.
    - Formulates cross-row and cross-column comparative questions (e.g. *Compare starting doses of Gabapentin vs Pregabalin across CKD Stages 3, 4, and 5*).
    - Ensures cell-level bounding boxes are preserved in provenance pointers.
- **Tests to Add / Update:** `tests/test_recipe_table_reasoning.py` — tests tabular QA generation from table assertion fixtures.
- **Definition of Done:** Table reasoning recipe outputs structured tabular comparative QA pairs.

#### Task 3.16: Route B Direct DOM Factoid QA Recipe
- **Files:** `app/synthetic/recipes/dom_direct.py`
- **Dependencies:** Tasks 3.1, 3.11, 3.12. **Blocks:** Task 3.30.
- **Expected Behavior (Fact | Research):**
  - Implement `DOMDirectRecipe(SynthesisRecipe)`:
    - Handles Route B narrative blocks (skipping graph extraction entirely).
    - Directly retrieves content-addressed text chunks from `app/chunking/`.
    - Synthesizes single-hop factoid Q&A pairs.
    - Attaches chunk-level provenance pointer `(doc_id, block_id, page_index)`.
- **Tests to Add / Update:** `tests/test_recipe_dom_direct.py` — tests direct DOM chunk generation without KG queries.
- **Definition of Done:** Route B recipe generates factoid QA at ~40% lower compute overhead.

#### Task 3.17: Adversarial Premise-Contradiction Recipe
- **Files:** `app/synthetic/recipes/contradiction.py`
- **Dependencies:** Tasks 2.12, 3.11, 3.12. **Blocks:** Task 3.30.
- **Expected Behavior (Fact | Research):**
  - Implement `ContradictionRecipe(SynthesisRecipe)`:
    - Formulates adversarial prompts with false medical premises (e.g. *Why is Metformin recommended at maximum dose in end-stage renal disease?*).
    - Trains model to explicitly reject the false premise in `<think>` and output the correct clinical contraindication.
- **Tests to Add / Update:** `tests/test_recipe_contradiction.py` — tests premise contradiction generation and validation.
- **Definition of Done:** Recipe outputs adversarial rejection datasets for clinical safety training.

#### Task 3.18: Gate 1 Evidence Entailment with DeBERTa-v3 NLI
- **Files:** `app/synthetic/trust/entailment.py`
- **Dependencies:** Tasks 1.1, 2.1, 3.1. **Blocks:** Task 3.24.
- **Expected Behavior (Fact | Research):**
  - Implement `EvidenceEntailmentGate`:
    - Cross-Encoder NLI model: `models/deberta-v3-nli` (`MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli`).
    - Compares premise (verbatim DOM evidence text) vs hypothesis (extracted assertion or synthesized fact).
    - Returns entailment probability score $\in [0.0, 1.0]$.
    - Hard Gate: Samples with entailment score $< 0.85$ are rejected (or marked unverified).
    - Runs on CPU (~15ms per pair) or during non-LLM GPU phases. Provides rule-based mock for hermetic testing.
- **Tests to Add / Update:** `tests/test_trust_gate1_entailment.py` — tests entailment scoring on entailment, neutral, and contradiction sentence pairs.
- **Definition of Done:** Gate 1 computes cross-encoder entailment and filters ungrounded assertions.

#### Task 3.19: Gate 2 Graph Consistency & Cycle Validation
- **Files:** `app/synthetic/trust/consistency.py`
- **Dependencies:** Tasks 2.10, 2.12, 3.1. **Blocks:** Task 3.24.
- **Expected Behavior (Fact):**
  - Implement `GraphConsistencyGate`:
    - Validates `GroundingSubgraph` inside `SyntheticRecord`.
    - Asserts absence of cycles in reasoning path (`len(set(node_path)) == len(node_path)`).
    - Asserts population homogeneity across all intermediate hops.
    - Rejects records violating ontological constraints.
- **Tests to Add / Update:** `tests/test_trust_gate2_consistency.py` — feeds valid and invalid cyclic subgraphs; asserts pass/reject decisions.
- **Definition of Done:** Gate 2 enforces topological integrity and population alignment.

#### Task 3.20: Gate 3 Physical DOM Provenance Audit
- **Files:** `app/synthetic/trust/provenance.py`
- **Dependencies:** Tasks 2.1, 3.1. **Blocks:** Task 3.24.
- **Expected Behavior (Fact):**
  - Implement `ProvenanceAuditGate`:
    - Validates every `ProvenancePointer` in `SyntheticRecord.provenance`.
    - Checks that `doc_id`, `page_index`, `block_id`, and `bbox` are non-null and structurally valid.
    - Verifies `source_text` matches actual text in the canonical DOM.
    - Hard Gate: Reject any synthetic record with fabricated or missing provenance pointers.
- **Tests to Add / Update:** `tests/test_trust_gate3_provenance.py` — tests provenance verification against canonical DOM fixture.
- **Definition of Done:** Gate 3 guarantees 100% provenance anchoring back to DOM blocks.

#### Task 3.21: Gate 4 PHI / PII Scrubbing with Presidio
- **Files:** `app/synthetic/trust/privacy.py`
- **Dependencies:** Task 1.1, Task 3.1. **Blocks:** Task 3.24.
- **Expected Behavior (Fact | Research):**
  - Implement `PrivacyScrubGate`:
    - Leverages `presidio-analyzer` and `presidio-anonymizer`.
    - Scans `instruction`, `reasoning_trace`, `target_response`, and `negative_response`.
    - Detects HIPAA PHI entities: `PERSON`, `PHONE_NUMBER`, `EMAIL_ADDRESS`, `US_SSN`, `DATE_TIME`, `LOCATION`, `MEDICAL_LICENSE`.
    - Hard Gate: Any record with unmasked real patient identifiers fails validation (`phi_free = False`) and is blocked from export.
- **Tests to Add / Update:** `tests/test_trust_gate4_privacy.py` — feeds text containing names, dates, phone numbers; verifies detection and masking.
- **Definition of Done:** Gate 4 ensures zero unmasked PHI leaks into synthetic records.

#### Task 3.22: Gate 5 Ragas Faithfulness Scorer against Verbatim DOM
- **Files:** `app/synthetic/trust/faithfulness.py`
- **Dependencies:** Task 1.1, Task 3.1. **Blocks:** Task 3.24.
- **Expected Behavior (Fact | Research):**
  - Implement `FaithfulnessGate`:
    - Evaluates semantic faithfulness of the generated response against verbatim source DOM text.
    - Computes statement-level context grounding using Ragas faithfulness metric (or deterministic token-overlap fallback when Ragas LLM unavailable).
    - Threshold: score $\ge 0.95$.
- **Tests to Add / Update:** `tests/test_trust_gate5_faithfulness.py` — tests faithfulness scoring on supported vs hallucinated answers.
- **Definition of Done:** Gate 5 scores and enforces factual faithfulness against verbatim document text.

#### Task 3.23: Gate 6 Self-BLEU & SimHash De-duplication
- **Files:** `app/synthetic/trust/dedup.py`
- **Dependencies:** Task 3.1. **Blocks:** Task 3.24.
- **Expected Behavior (Fact | Research):**
  - Implement `DeDuplicationGate`:
    - Calculates 64-bit SimHash of `instruction` and `target_response`.
    - Maintains in-memory SimHash index with Hamming distance threshold $\le 3$ to detect near-duplicates.
    - Computes batch Self-BLEU across generated instructions (threshold $\le 0.35$) to prevent repetitive phrasing collapse.
- **Tests to Add / Update:** `tests/test_trust_gate6_dedup.py` — tests exact and near-duplicate detection via SimHash; tests Self-BLEU calculation.
- **Definition of Done:** Gate 6 rejects redundant and semantically repetitive synthetic records.

#### Task 3.24: Trust Engine Master Pipeline
- **Files:** `app/synthetic/trust/engine.py`, `app/synthetic/trust/__init__.py`
- **Dependencies:** Tasks 3.18 - 3.23. **Blocks:** Task 3.30.
- **Expected Behavior (Fact):**
  - Implement `TrustVerificationEngine`:
    - Chains Gates 1 through 6 in strict sequence:
      1. Gate 1: Evidence Entailment
      2. Gate 2: Graph Consistency
      3. Gate 3: Physical Provenance Audit
      4. Gate 4: PHI / PII Scrubbing
      5. Gate 5: Ragas Faithfulness
      6. Gate 6: De-duplication
    - Attaches comprehensive `QualityMetadata` to passing records.
    - Discards or logs rejected records with detailed gate failure diagnostics.
- **Tests to Add / Update:** `tests/test_trust_engine.py` — runs records through all 6 gates; verifies rejection when any single gate fails.
- **Definition of Done:** Master trust engine coordinates all 6 hard gates and certifies records for export.

#### Task 3.25: Export Format Protocol & Registry
- **Files:** `app/synthetic/exporter/protocol.py`, `app/synthetic/exporter/__init__.py`
- **Dependencies:** Task 3.1. **Blocks:** Tasks 3.26 - 3.29.
- **Expected Behavior (Fact):**
  - Define `ExportFormat` `@runtime_checkable` Protocol:
    ```python
    class ExportFormat(Protocol):
        @property
        def format_name(self) -> str: ...
        def export(self, records: list[SyntheticRecord], output_path: Path) -> Path: ...
    ```
  - Implement `ExporterRegistry` to register and look up exporters by format string (`"chatml"`, `"alpaca"`, `"dpo"`, `"parquet"`).
- **Tests to Add / Update:** `tests/test_exporter_registry.py` — tests exporter registration and retrieval.
- **Definition of Done:** Export protocol and registry ready for format adapters.

#### Task 3.26: ChatML Exporter with `<think>` CoT Tags
- **Files:** `app/synthetic/exporter/chatml.py`
- **Dependencies:** Tasks 3.1, 3.25. **Blocks:** Task 3.30.
- **Expected Behavior (Fact | Research):**
  - Implement `ChatMLExporter(ExportFormat)`:
    - Serializes records to JSONL matching OpenAI / Qwen / DeepSeek ChatML format:
      ```json
      {
        "id": "syn-rec-12345",
        "messages": [
          {"role": "system", "content": "You are an expert clinical pharmacologist..."},
          {"role": "user", "content": "...instruction..."},
          {"role": "assistant", "content": "<think>\n...reasoning_trace...\n</think>\n\n...target_response..."}
        ],
        "provenance": [...]
      }
      ```
- **Tests to Add / Update:** `tests/test_exporter_chatml.py` — exports synthetic records; asserts JSONL schema and `<think>` encapsulation.
- **Definition of Done:** ChatML exporter writes valid JSONL files readable by standard SFT trainers.

#### Task 3.27: Alpaca Instruction Exporter
- **Files:** `app/synthetic/exporter/alpaca.py`
- **Dependencies:** Tasks 3.1, 3.25. **Blocks:** Task 3.30.
- **Expected Behavior (Fact):**
  - Implement `AlpacaExporter(ExportFormat)`:
    - Serializes to standard Alpaca JSON format:
      ```json
      {
        "instruction": "...instruction...",
        "input": "",
        "output": "...reasoning_trace + target_response..."
      }
      ```
- **Tests to Add / Update:** `tests/test_exporter_alpaca.py` — tests export and validation against Alpaca schema.
- **Definition of Done:** Alpaca exporter writes standard instruction-tuning JSON.

#### Task 3.28: DPO Preference Arrow / Parquet Exporter
- **Files:** `app/synthetic/exporter/dpo_arrow.py`
- **Dependencies:** Tasks 3.1, 3.14, 3.25. **Blocks:** Task 3.30.
- **Expected Behavior (Fact | Research):**
  - Implement `DPOArrowExporter(ExportFormat)`:
    - Serializes DPO preference pairs to HuggingFace / PyArrow Dataset format with columns:
      - `prompt`: The clinical instruction.
      - `chosen`: The verified `<think>` trace + gold response.
      - `rejected`: The counterfactually perturbed `<think>` trace + false response.
      - `metadata`: Subgraph path ID, provenance block IDs, entailment scores.
- **Tests to Add / Update:** `tests/test_exporter_dpo.py` — exports DPO records to Arrow table; validates columns and values.
- **Definition of Done:** DPO preference datasets serialized cleanly to PyArrow / Parquet.

#### Task 3.29: HuggingFace Parquet Exporter
- **Files:** `app/synthetic/exporter/huggingface.py`
- **Dependencies:** Tasks 3.1, 3.25. **Blocks:** Task 3.30.
- **Expected Behavior (Fact):**
  - Implement `HuggingFaceExporter(ExportFormat)`:
    - Serializes full `SyntheticRecord` lists into columnar Parquet files with embedded metadata and quality metrics.
- **Tests to Add / Update:** `tests/test_exporter_hf.py` — verifies Parquet writing, column schemas, and read-back via PyArrow.
- **Definition of Done:** Parquet dataset exported with zero data corruption.

#### Task 3.30: Synthetic Engine CLI & Packaging
- **Files:** `app/synthetic/cli.py`, `app/synthetic/__init__.py`
- **Dependencies:** Tasks 3.1 - 3.29. **Blocks:** Gate 4 execution.
- **Expected Behavior (Fact):**
  - CLI entry point: `python -m app.synthetic.cli generate`:
    - Options: `--doc-id <ID>`, `--recipe [multi_hop|dpo|table|direct]`, `--count <N>`, `--format [chatml|alpaca|dpo|parquet]`, `--out <PATH>`.
    - Coordinates extraction -> routing -> sampling -> GRA triad -> Trust Engine -> export.
  - Package exports in `app/synthetic/__init__.py`.
- **Tests to Add / Update:** `tests/test_synthetic_cli.py` — tests CLI arguments parsing, help message, and end-to-end dry run.
- **Definition of Done:** CLI operational and package exports cleanly configured.

---

### Track 4: Evaluation Suites & Gold Assertion Benchmarks (`eval/kg/`)

#### Task 4.1: Gold Benchmark Corpora & Validation Fixtures
- **Files:** `eval/kg/gold_sets/contraindications_negation.json`, `eval/kg/gold_sets/dosage_adjustments_renal.json`, `eval/kg/gold_sets/drug_drug_interactions.json`
- **Dependencies:** None. **Blocks:** Tasks 4.2, 4.3.
- **Expected Behavior (Fact | Research):**
  - Curate and validate gold benchmark assertion files in `eval/kg/gold_sets/`:
    1. `contraindications_negation.json`: Curated guideline snippets with strict negation requirements (e.g. Duloxetine in severe renal impairment, Metformin in lactic acidosis risk).
    2. `dosage_adjustments_renal.json`: Curated dosage reduction guidelines with exact eGFR/CrCl thresholds and numerical dosage ceilings (e.g. Pregabalin $\le 25$ mg, Gabapentin 200–700 mg).
    3. `drug_drug_interactions.json`: Pharmacokinetic/pharmacodynamic interaction pairs (e.g. Warfarin + CYP2C9 inhibitors, Apixaban + dual antiplatelets).
- **Tests to Add / Update:** `tests/test_eval_gold_sets.py` — asserts JSON validity, schema compliance, non-empty assertions, and valid canonical entity IDs.
- **Definition of Done:** Gold sets validated and checked into repository.

#### Task 4.2: Qualified Assertion Evaluator Harness
- **Files:** `eval/kg/assertion_evaluator.py`
- **Dependencies:** Tasks 2.1, 4.1. **Blocks:** Task 4.3.
- **Expected Behavior (Fact):**
  - Review and harden `eval/kg/assertion_evaluator.py`:
    - Computes:
      - Subject & Object Canonical ID Linking: Precision, Recall, F1.
      - Predicate Accuracy & Confusion Matrix.
      - Qualifier Fidelity: Negation Accuracy (critical for safety), Population Match Rate, Dosage Ceiling Preservation.
      - Provenance Anchoring: Percentage of predictions linked to valid DOM `Evidence` blocks.
    - Generates formatted terminal reports and structured JSON summaries.
- **Tests to Add / Update:** `tests/test_assertion_evaluator.py` — feeds simulated predictions against gold benchmark; asserts precision, recall, and qualifier accuracy calculations.
- **Definition of Done:** Assertion evaluator computes multi-dimensional fidelity metrics deterministically.

#### Task 4.3: Synthetic Quality & Faithfulness Benchmark Suite
- **Files:** `eval/kg/suite.py`, `eval/kg/metrics/`
- **Dependencies:** Tasks 3.1, 3.24, 4.2. **Blocks:** Gate 6 (Quality Review).
- **Expected Behavior (Fact | Research):**
  - Implement evaluation runner `eval/kg/suite.py`:
    - Runs end-to-end evaluation across:
      1. Extraction fidelity on gold sets.
      2. Trust Gate pass rates (Entailment, Consistency, Provenance, PHI, Faithfulness, Diversity).
      3. Generation diversity metrics: Self-BLEU, vocabulary entropy, instruction length distribution.
    - CLI: `python -m eval.kg.suite --gold-dir eval/kg/gold_sets/ --output eval_report.json`.
- **Tests to Add / Update:** `tests/test_eval_suite.py` — runs evaluation suite on mock predictions; asserts report output structure.
- **Definition of Done:** Comprehensive evaluation suite operational, reporting verifiable accuracy across model revisions.

---

### Track 5: Integration, Regression & Smoke Verification

#### Task 5.1: Unit & Integration Regression Suite
- **Files:** `tests/test_kg_*.py`, `tests/test_synthetic_*.py`
- **Dependencies:** All previous tasks. **Blocks:** Task 5.2.
- **Expected Behavior (Fact):**
  - Ensure all unit tests run hermetically under `.venv/Scripts/python.exe -m pytest tests/ -q`.
  - Zero crashes, zero unhandled exceptions, zero network dependencies during unit tests.
- **Tests to Add / Update:** Comprehensive test coverage across all newly created modules.
- **Definition of Done:** Full pytest suite passes green cleanly.

#### Task 5.2: End-to-End Pipeline Smoke Test
- **Files:** `tests/test_kg_synthetic_e2e.py`
- **Dependencies:** Task 5.1. **Blocks:** Gate 4 sign-off.
- **Expected Behavior (Fact | Research):**
  - End-to-end integration smoke driver:
    - Input: Canonical DOM document containing narrative text and a clinical table.
    - Step 1: Stage 1 GLiNER mentions extraction -> entity linking to RxNorm/MeSH.
    - Step 2: Stage 2 structured relation extraction + table relational extraction.
    - Step 3: Persistence into DuckDB store + serialization to `kg-v1.0.0.json`.
    - Step 4: Subgraph sampling of 2-hop clinical reasoning chain.
    - Step 5: GRA Quality Triad generates candidate with `<think>` trace.
    - Step 6: 6-Gate Trust Engine certifies record.
    - Step 7: Exporter writes ChatML, Alpaca, and DPO Arrow outputs.
- **Tests to Add / Update:** `tests/test_kg_synthetic_e2e.py::test_end_to_end_synthesis_pipeline`.
- **Definition of Done:** Complete document-to-synthetic pipeline executes cleanly from start to finish.

---

## 4. Test Plan & Verification Strategy

### Layer 1: Hermetic Unit Tests (100% Offline, Fast)
- **Execution:** Runs in standard CI and local dev within < 30 seconds.
- **Coverage:** Pydantic models, ontology lookups (FlashText/RapidFuzz), DuckDB schema & CTE walks, router decision logic, mock LLM generation, GRA triad state machine, SimHash de-duplication, and exporters.
- **Mocking Strategy:** Uses `MockLLMClient`, `MockGLiNER`, and rule-based entailment mock to avoid model downloads.

### Layer 2: Model-Gated Integration Tests (Local GPU / Heavy)
- **Execution:** Gated with `@pytest.mark.skipif(not os.path.exists("models/..."))`.
- **Components:**
  - Real GLiNER candidate extraction (`models/gliner_large-v2.1`).
  - Real GGUF inference (`models/phi4-mini/Phi-4-mini-instruct-Q4_K_M.gguf`).
  - Real DeBERTa-v3 cross-encoder NLI (`models/deberta-v3-nli`).
- **Memory Invariant:** Sequential execution only (`_GPU_LOCK` enforced), peak VRAM < 3.5 GB, zero `std::bad_alloc`.

### Layer 3: Empirical Gold Assertion Benchmark
- **Execution:** Executed via `python -m eval.kg.suite`.
- **Target Metrics:**
  - Entity-Pair Linking F1 $\ge 90.0\%$.
  - Predicate Accuracy $\ge 88.0\%$.
  - Negation Qualifier Accuracy $\ge 98.0\%$ (critical safety constraint).
  - Dosage Ceiling Accuracy $\ge 92.0\%$.
  - Physical Provenance Anchoring: $100.0\%$.

---

## 5. Edge Cases, Guardrails & Mitigations

| Risk / Edge Case | Architectural Root Cause | Mitigation in Implementation Plan |
| :--- | :--- | :--- |
| **Negation Inversion (Safety Hazard)** | LLMs often omit "not", turning contraindications into lethal recommendations. | Gate 1 DeBERTa NLI cross-encoder directly evaluates contradiction vs entailment; evaluator explicitly tracks negation accuracy ($\ge 98\%$ target). |
| **Spatial Provenance Nullability** | Scanned or OCR blocks occasionally have missing or shifted bboxes. | `Evidence.bbox` is nullable; Provenance Audit Gate verifies presence of verbatim text match even when physical bbox is approximated. |
| **Circular Routing Dependency** | Router needing graph data before graph extraction occurs. | Router strictly uses Stage 1 GLiNER mention counts and `Block.kind == "table"` *before* any graph insertion or LLM calls. |
| **DuckDB CTE Infinite Recursion** | Cyclic graphs causing infinite loops during multi-hop path sampling. | CTE enforces `NOT list_contains(p.path_ids, a2.id) AND NOT list_contains(p.node_path, a2.object_id)` and hard depth ceiling (`hops < 4`). |
| **VRAM Out of Memory (RTX 3050 4 GB)** | Concurrent loading of Phi-4-mini GGUF, GLiNER, and DeBERTa. | Single-model residency enforced via `_GPU_LOCK`: GLiNER and DeBERTa default to CPU; Phi-4-mini resides in VRAM only during generation. |
| **PHI / PII Data Leakage** | Enterprise medical PDFs containing patient names, MRNs, or dates. | Gate 4 Presidio analyzer + anonymizer enforces 0 unmasked entities before records can be written to disk. |

---

## 6. Implementation Rollout Schedule & Gate Transitions

- **Phase 1 (Setup):** Tasks 1.1 - 1.2 -> Requirements and model downloader.
- **Phase 2 (Module #5 `app/kg`):** Tasks 2.1 - 2.13 -> Ontologies, Extractors, DuckDB Store, Serialization.
- **Phase 3 (Module #6 `app/synthetic`):** Tasks 3.1 - 3.30 -> LLM Seam, Router, Sampler, GRA Triad, 6 Gates, Exporters.
- **Phase 4 (Evaluation `eval/kg`):** Tasks 4.1 - 4.3 -> Gold sets, Evaluator, Benchmark Suite.
- **Phase 5 (Verification):** Tasks 5.1 - 5.2 -> Green test suite, End-to-end smoke test.

Upon completion of this plan by the Technical Planner, the run advances to **Gate 4 (Implementation Engineer)**.

---

PLAN: READY
