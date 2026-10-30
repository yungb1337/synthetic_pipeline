# Architecture Re-Review Report: Gate 5 Execution (Post Fix Round 1)

**Run ID:** `run-2026-10-21-synthetic-kg-architecture`  
**Architecture Specification:** Architecture Gamma ("Dual-Route Adaptive with GRA Quality Triad")  
**Target Modules:** Module #5 (`app/kg`), Module #6 (`app/synthetic`), and Evaluation Suites (`eval/kg/`)  
**Status:** Audited against approved architecture, ADRs, clean architecture boundaries, hardware guardrails, and Fix Round 1 implementations.  
**Review Verdict:** PASS (All 8 prior findings verified resolved; 0 remaining blocking, major, or minor architectural issues).

---

## 1. Verification of Fix Round 1 Remediation Items

### [BLOCKING-01] Statement Grounding Fallback Logic & Ragas Integration Seam
- **File:** `app/synthetic/trust/faithfulness.py`
- **Lines:** 20–32, 81–94, 96–114
- **Verification (Fact):**
  The flawed fallback branch `any(term in source_lower for term in (...))` has been replaced with strict, bidirectional sentence-level grounding. The gate requires either $\ge 50\%$ word overlap against source text, OR clinical terms asserted in the generated sentence (`sentence_clinical`) must all be present in the source excerpt alongside $\ge 35\%$ content-word overlap:
  ```python
  # Check if clinical terms asserted in sentence are present in source
  sentence_clinical = [w for w in sent_words if w in clinical_terms]

  # A sentence is supported if significant key terminology appears in source
  if word_ratio >= 0.50:
      supported_count += 1
  elif (
      sentence_clinical
      and all(term in source_lower for term in sentence_clinical)
      and word_ratio >= 0.35
  ):
      supported_count += 1
  ```
  The `evaluate_ragas_faithfulness()` integration seam is present at lines 20–32 with a protected `(ImportError, Exception)` boundary. `FaithfulnessGate.evaluate()` conforms to `TrustGate(Protocol)` returning typed `GateResult`.
- **Status:** RESOLVED.

---

### [MAJOR-01] Microsoft Presidio PHI / PII Scrubbing Integration
- **File:** `app/synthetic/trust/privacy.py`
- **Lines:** 34–68, 93–118, 124–154, 156–205
- **Verification (Fact):**
  Microsoft Presidio `AnalyzerEngine` and `AnonymizerEngine` are integrated via `_get_presidio_engines()`. The initialization sequence inspects installed spaCy packages (`en_core_web_lg`, `en_core_web_md`, `en_core_web_sm`) and gracefully falls back to `spacy.blank("en")` to prevent blocking network downloads on constrained environments, with a complete regex fallback on exception:
  ```python
  if spacy_model:
      nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": spacy_model}])
  else:
      nlp = spacy.blank("en")
      nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": "en"}])
      nlp_engine.nlp = {"en": nlp}

  _analyzer_engine = AnalyzerEngine(nlp_engine=nlp_engine)
  _anonymizer_engine = AnonymizerEngine()
  ```
  False-positive clinical suppression for `DATE_TIME` (dosage amounts e.g. `25 mg` and frequencies `daily`, `bid`) is enforced at lines 103–109. `PrivacyScrubGate.evaluate()` returns typed `GateResult` with detected entity metadata.
- **Status:** RESOLVED.

---

### [MAJOR-02] Layering Inversion Decoupling (Module #5 vs Module #6)
- **Files:**
  - `app/kg/extractor/protocol.py` (Lines 15–28)
  - `app/kg/extractor/relations.py` (Line 13)
- **Verification (Fact):**
  The inverted dependency from upstream extraction (`app/kg`) into downstream synthesis (`app/synthetic`) has been eliminated. An independent extraction LLM interface is defined in `app/kg/extractor/protocol.py`:
  ```python
  @runtime_checkable
  class ExtractorLLMClient(Protocol):
      """Protocol for LLM client used during structured relation extraction."""

      def structured_predict(
          self,
          prompt: str,
          response_model: type[T],
          system_prompt: str | None = None,
          temperature: float = 0.1,
      ) -> T:
          ...
  ```
  `StructuredRelationExtractor` imports `ExtractorLLMClient` exclusively from `app.kg.extractor.protocol`. Codebase search confirmed zero occurrences of `app.synthetic` inside `app/kg/`.
- **Status:** RESOLVED.

---

### [MAJOR-03] DuckDB Recursive CTE Population Filter Leak
- **File:** `app/kg/store/store.py`
- **Lines:** 215–233
- **Verification (Fact):**
  In `walk_paths()`, the recursive projection preserves population qualification across intermediate unpopulated steps using `COALESCE(NULLIF(p.population, ''), a2.population)`:
  ```sql
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
    AND NOT list_contains(p.path_ids, a2.id)
    AND NOT list_contains(p.node_path, a2.object_id)
    AND (p.population = '' OR a2.population = '' OR p.population = a2.population)
  ```
  If hop 1 sets `population = 'Stage 4 CKD'` and hop 2 has an empty population (`''`), `COALESCE` propagates `'Stage 4 CKD'`. In hop 3, an incompatible population (e.g., `'Pediatric'`) is rejected by the filter.
  Additionally, the previous N+1 query loop was vectorized at lines 246–262 via `WHERE id IN (...)`.
- **Status:** RESOLVED.

---

### [MINOR-01] Canonical Instruction Parameter for DPO Perturbation
- **Files:**
  - `app/synthetic/quality/generator_agent.py` (Lines 31–53, 82–84)
  - `app/synthetic/recipes/dpo_perturbation.py` (Lines 48–53)
- **Verification (Fact):**
  `GeneratorAgent.generate_from_path()` accepts `instruction: str | None = None`. When passed, the prompt explicitly directs the model to answer the chosen prompt without generating a divergent question. `DPOPerturbationRecipe` passes `instruction=refined_chosen.instruction` when synthesizing `rejected_candidate`, ensuring chosen and rejected responses address the identical clinical query.
- **Status:** RESOLVED.

---

### [MINOR-02] Tabular Assertion Filter Hardening
- **File:** `app/synthetic/recipes/table_reasoning.py`
- **Lines:** 32–40
- **Verification (Fact):**
  The brittle substring filter was replaced with strict table block and source checks:
  ```sql
  SELECT DISTINCT a.id
  FROM assertions a
  JOIN evidence ev ON a.id = ev.assertion_id
  WHERE ev.block_id LIKE 'tbl-%' OR a.guideline_source = 'Table'
  LIMIT 50
  ```
- **Status:** RESOLVED.

---

### [MINOR-03] Unified `TrustGate` Protocol & Typed `GateResult` Interface
- **Files:**
  - `app/synthetic/trust/protocol.py` (Lines 11–28)
  - `app/synthetic/trust/engine.py` (Lines 48–98)
  - `app/synthetic/trust/entailment.py` (Line 91)
  - `app/synthetic/trust/consistency.py` (Lines 46, 59, 65)
  - `app/synthetic/trust/provenance.py` (Lines 63, 73, 80)
  - `app/synthetic/trust/privacy.py` (Lines 184, 192, 200)
  - `app/synthetic/trust/faithfulness.py` (Lines 99, 113)
  - `app/synthetic/trust/dedup.py` (Lines 101, 111, 124, 139)
- **Verification (Fact):**
  `TrustGate` protocol is standardized via `@runtime_checkable class TrustGate(Protocol)` requiring `evaluate(record: SyntheticRecord, **kwargs: Any) -> GateResult`. All 6 gates implement this protocol and return `GateResult(passed, score, message, metadata)`. `TrustVerificationEngine.verify_record()` uniformly processes results without ad-hoc tuple unpacking.
- **Status:** RESOLVED.

---

### [MINOR-04] Duplicate DDL Schema Removal
- **Files:**
  - Deleted: `app/kg/duckdb_schema.sql`
  - Canonical: `app/kg/store/duckdb_schema.sql`
- **Verification (Fact):**
  Confirmed that `app/kg/duckdb_schema.sql` was removed. The single source of truth for the Option 2 normalized relational schema is preserved at `app/kg/store/duckdb_schema.sql`.
- **Status:** RESOLVED.

---

## 2. Review Checklist Findings

### 1. Consistency with Approved Architecture & ADRs
- **Dual-Route Routing (Architecture Gamma §3):** `DualRouteRouter` (`app/synthetic/router.py`) deterministically routes blocks based on `block.kind == "table"` and Stage 1 mention counts before graph insertion, preventing circular dependencies.
- **Option 2 Normalized Schema (Architecture Gamma §6, Decision 1):** Tripartite relational schema (`entities`, `assertions`, `evidence`) is maintained in DuckDB with foreign key integrity.
- **Rebuildable Projection (ADR-008, Architecture Gamma §6, Decision 5):** `GraphSerializer` (`app/kg/store/serializer.py`) outputs content-addressed artifacts (`kg-v{version}.json`, `kg-summary-v{version}.json`, `entity-index-v{version}.json`) enabling deterministic projection restoration.
- **Hardware Envelope Respect (Architecture Gamma §0):** Single-model GPU residency rule is enforced in `LlamaCppClient` (`app/synthetic/llm/llama_cpp.py`) via `_GPU_LOCK = threading.Lock()`. GLiNER and DeBERTa default to CPU execution, keeping peak RAM within the 16.5 GB host / 4 GB VRAM envelope.

### 2. Coupling / Decoupling & Clean Interfaces
- **Extraction to Synthesis Decoupling:** Upstream extraction (Module #5) has zero imports from downstream synthesis (Module #6).
- **Legacy Module Isolation:** Modules #1–#4 (`app/parser`, `app/normalizer`, `app/chunking`, `app/embedding`, `app/routing`, `app/processing`) contain zero imports of `app.kg` or `app.synthetic`.
- **Protocol Seams:** All seams utilize `@runtime_checkable` Python Protocols (`OntologyProvider`, `EntityExtractor`, `RelationExtractor`, `TableExtractor`, `GraphStore`, `ExtractorLLMClient`, `LLMClient`, `SynthesisRecipe`, `TrustGate`, `ExportFormat`).

### 3. Single Responsibility & Boundary Integrity
- **Canonical DOM Immutability:** Canonical DOM entities (`Document`, `Page`, `Block`, `Table`, `Cell`, `BBox`) from `app.parser.dom.models` are strictly consumed as read-only inputs.
- **Lineage Integrity:** Every extracted assertion and synthetic record retains physical provenance pointers (`doc_id`, `page_index`, `block_id`, `bbox`, `source_text`).

### 4. Future-Proofing & Deferred Choices
- Deferred capabilities from Architecture Gamma §7 (Neo4j/Memgraph cluster, Leiden community detection, character-offset DOM alignment, multi-agent debate simulation, streaming KG updates) remain excluded from v1 without precluding future adoption via existing protocol seams (`GraphStore`, `LLMClient`).

### 5. Layering vs `docs/*.md` Specs
- Conforms to `docs/universal-document-understanding-engine.md` §0: the Knowledge Graph and Synthetic Generation systems operate as decoupled consumers/projections of the Canonical DOM.

---

## 3. Ordered Defect Inventory

### Blocking Issues
- None.

### Major Issues
- None.

### Minor Issues
- None.

---

VERDICT: PASS
