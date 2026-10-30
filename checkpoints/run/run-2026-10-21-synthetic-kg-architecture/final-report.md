# Final Report — `run-2026-10-21-synthetic-kg-architecture`

**Run id:** `run-2026-10-21-synthetic-kg-architecture` · **Date:** 2026-10-31 · **Modules:** Module #5 (`app/kg`), Module #6 (`app/synthetic`), Evaluation Suite (`eval/kg`)

---

## 1. Objective
Transform enterprise clinical and healthcare documentation from the Canonical DOM into **privacy-preserving, explainable, validated synthetic training datasets**, founded on the organizational thesis: *"Trust & verification is the product, not the synthetic records themselves."* 

Implement production-ready Knowledge Graph extraction grounded in pharmacology ontologies (RxNorm, MeSH), multi-hop graph path sampling via DuckDB recursive CTEs, model-agnostic local/API LLM orchestration under strict 4 GB VRAM constraints, multi-agent peer review (Generator-Reviewer-Adjudicator Triad), and a 6-Gate Trust & Verification Engine.

---

## 2. Architecture Overview: Architecture Gamma
**Architecture Gamma ("Dual-Route Adaptive with GRA Quality Triad")** introduces:
1. **Tripartite Relational DuckDB Store (Option 2):** Normalized tables (`entities`, `assertions`, `evidence`) with composite foreign keys and indexes. 2–4 hop paths are traversed using vectorized recursive Common Table Expressions with array cycle prevention (`NOT list_contains(path_ids, a2.id)`) and patient population homogeneity constraints (`COALESCE(NULLIF(p.population, ''), a2.population)`). Graph data operates as an immutable rebuildable projection from `kg-v{version}.json`.
2. **Strict Hardware Envelope (16.5 GB RAM / 4 GB VRAM):** Standardizes local generation on `microsoft/Phi-4-mini-instruct` (Q4_K_M GGUF, ~2.32 GB) serialized via a class-level `_GPU_LOCK = threading.Lock()`. Mention extraction (GLiNER) and entailment (DeBERTa-v3) default to CPU (`device="cpu"`), preventing VRAM paging and CUDA out-of-memory crashes.
3. **Model-Agnostic LLM Client Seam:** Decoupled `LLMClient` protocol supporting local GGUF (`llama-cpp-python`), Gemini (authenticated securely via `x-goog-api-key` header), OpenAI, and hermetic Mock clients with unified JSON parsing in `structured_predict_helper()`.
4. **Generator-Reviewer-Adjudicator (GRA) Quality Triad:** Multi-agent peer review loop eliminating single-judge self-enhancement, verbosity, and position biases.
5. **Non-Circular Pre-Routing:** `DualRouteRouter` inspects block layout (`block.kind == "table"`) and Stage 1 mention counts before graph insertion, routing narrative text to direct chunk QA and rich relational text to multi-hop KG traversal.
6. **6-Gate Trust & Verification Engine:** Unified under `@runtime_checkable class TrustGate(Protocol) -> GateResult`:
   - Gate 1: Evidence Entailment (DeBERTa-v3 cross-encoder NLI $\ge 0.85$).
   - Gate 2: Graph Consistency (Topological cycle check & population alignment).
   - Gate 3: Physical DOM Provenance Audit (`doc_id`, `page_index`, `block_id`, `bbox`, `verbatim_text`).
   - Gate 4: HIPAA PHI/PII Scrubbing (Microsoft Presidio `AnalyzerEngine`/`AnonymizerEngine` with safe blank spaCy fallback and dosage false-positive suppression).
   - Gate 5: Sentence-Level Faithfulness Grounding ($\ge 0.95$ word and keyterm overlap with protected Ragas integration seam).
   - Gate 6: De-duplication (Bounded SimHash Hamming distance $\le 3$ and batch Self-BLEU $\le 0.35$).

---

## 3. Gate Pipeline Execution & Verdicts

| Gate | Stage | Agent / Artifact | Status | Verdict |
|---|---|---|---|---|
| Gate 0 | Run Brief Init | `project_memory/active_objective.md` | Initialized | **COMPLETE** |
| Gate 1 | Research Swarm | `checkpoints/run/<id>/research.md` | Verified | `RESEARCH: COMPLETE` |
| Gate 2 | Chief Architect | `checkpoints/run/<id>/architecture.md` | Verified | `ARCHITECTURE: APPROVED` |
| Gate 3 | Technical Planner | `checkpoints/run/<id>/implementation-plan.md` | Verified | `PLAN: READY` |
| Gate 4 | Implementation Engineer | `engineer-report.md` + code in `app/kg/`, `app/synthetic/` | Complete | **IMPLEMENTED** |
| Gate 5 | Architecture Review | `reviews/architecture.md` | Audited | `VERDICT: FAIL` (8 findings) |
| Gate 6 | Quality & Perf Review | `reviews/quality.md` | Audited | `VERDICT: PASS with recommendations` |
| Fix Loop | Engineer Fix Round 1 | `engineer-report-fix-round-1.md` | 13 issues remediated | **COMPLETE** |
| Gate 5 | Architecture Re-Review | `reviews/architecture.md` | Re-audited | `VERDICT: PASS` |
| Gate 6 | Quality Re-Review | `reviews/quality.md` | Re-audited | `VERDICT: PASS` |
| Gate 7 | Knowledge Curator | `project_memory/` + `checkpoint.md` | Curated | **CHECKPOINT COMPLETE** |

---

## 4. Test Verification Status
- **Focused KG & Synthetic Test Suite:**
  `pytest tests/test_kg_module.py tests/test_synthetic_module.py tests/test_kg_synthetic_e2e.py tests/test_eval_kg.py -q`
  → **45 passed in 1.48s (100% pass rate)**.
- **Full Repository Regression Suite:**
  `pytest tests/ -q`
  → **435 passed, 2 skipped (optional PDF fixtures), 0 failures in 128s**.
- **Code Duplication & Similarity Signal:**
  `python scripts/check_similarity.py`
  → 0 code-duplication regressions. `structured_predict()` logic unified in `protocol.py`; redundant root schema removed; Module #5 has 0 imports from Module #6.
- **Hermeticity:** 100% offline execution. No neural weights are fetched during CI/test execution; mock LLM clients, regex-gazetteer extractors, and heuristic NLI fallbacks allow full verification without external network access.

---

## 5. Architecture Decision Record
**ADR-015: Dual-Route Adaptive Architecture with GRA Quality Triad & Normalized DuckDB Knowledge Graph** appended to `project_memory/architecture_decisions.md`.

---

## 6. What Changed / Where It Left Off

### New Codebases & Deliverables
- `app/kg/`:
  - `models.py`: Pydantic models for `Entity`, `Assertion`, `Evidence`, `SubgraphPath`.
  - `ontology/`: `OntologyProvider` protocol, `OntologyRegistry`, `PharmacologyOntologyProvider` with FlashText exact and RapidFuzz fuzzy matching over RxNorm and MeSH.
  - `extractor/`: Stage 1 zero-shot mentions (`mentions.py`), Stage 2 structured relations (`relations.py`) using `ExtractorLLMClient`, table extraction (`tables.py`), coreference resolution (`coreference.py`).
  - `store/`: `DuckDBGraphStore` (`store.py`), Option 2 relational schema (`duckdb_schema.sql`), and content-addressed immutable projection serializer (`serializer.py`).
  - `validation.py`: Contradiction detection and referential integrity verification.
- `app/synthetic/`:
  - `llm/`: `LLMClient` protocol, `LlamaCppClient` (with `_GPU_LOCK`), `APIClient` (Gemini/OpenAI), and `MockLLMClient`.
  - `router.py`: `DualRouteRouter` heuristic layout and mention pre-routing.
  - `sampler.py`: Subgraph topology sampler with recursive CTE walks and dynamic seed limits.
  - `quality/`: Generator-Reviewer-Adjudicator (GRA) peer-review triad.
  - `recipes/`: Multi-hop CoT reasoning, DPO perturbation, Table reasoning, DOM direct, and Contradiction recipes.
  - `trust/`: Standardized 6-Gate Trust & Verification Engine (`TrustGate(Protocol) -> GateResult`).
  - `exporter/`: ChatML JSONL (`<think>` CoT), Stanford Alpaca JSON, DPO Arrow, and Parquet.
  - `cli.py`: CLI entry point (`python -m app.synthetic.cli generate`).
- `eval/kg/`:
  - Curated gold evaluation benchmarks (`contraindications_negation.json`, `drug_drug_interactions.json`).
  - Assertion metrics, vocabulary entropy, and Self-BLEU diversity scorers.
- `scripts/download_kg_models.py`:
  - Automated downloader for GLiNER, Phi-4-mini GGUF, and DeBERTa-v3 NLI.

### Tracked Next Capabilities (project_memory/questions.md)
- Multi-node distributed graph cluster (Neo4j / Memgraph) for cross-tenant enterprise scaling.
- Leiden community detection for corpus-wide hierarchical thematic summaries.
- Large-scale synthetic generation runs across the full 1,945-document dual corpus.

---

**Status: COMPLETE.** All definition-of-done items met; all gates passed green.
