# Master Research & Architecture Blueprint: Knowledge Graph & Ontology-Grounded Synthetic Data Generation Platform (MedFactory AI)

**Author:** Senior ML / Data-System Researcher & Architect  
**Project:** MedFactory AI / Synthetic Data Factory  
**Status:** Approved Research & Architecture Specification  
**Linked Sub-Documents:**
- [papers.md](research/papers.md) — Systematic Literature Review (2021–2026)
- [kg_construction.md](research/kg_construction.md) — Knowledge Graph Construction & Lineage Specification
- [synthetic_generation_methods.md](research/synthetic_generation_methods.md) — 13 Synthetic Generation Methods & KG Critical Analysis
- [evaluation.md](research/evaluation.md) — 12-Dimensional Synthetic Data Evaluation Framework
- [architecture_decisions.md](research/architecture_decisions.md) — System Decision Record, Pipeline Life Cycle, & Experiments

---

## 1. Executive Understanding of the System

### The Core Vision: Trust & Provenance as the Product
In enterprise and regulated domains (specifically healthcare), synthetic data generation cannot rely on unconstrained, stochastic LLM generation. Standard tools (such as `meta-llama/synthetic-data-kit`) ingest raw text and prompt an LLM to invent questions and reasoning chains. While fluent, this approach frequently produces **hallucinated multi-step leaps**, **uncontrolled reasoning complexity**, **superficial shortcuts**, and **zero mathematical provenance**.

**MedFactory AI** establishes an entirely different paradigm:
> **We transform an enterprise's proprietary documents into a verified Knowledge Graph (the single source of truth), sample deterministic graph topologies ($k$-hop chains, intersections, table joins), and verbalize them into provenance-anchored synthetic instruction and preference datasets where every single fact is mathematically traceable back to source document bounding boxes.**

---

## 2. The Complete End-to-End System Lifecycle

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                1. DOCUMENT INGESTION & PARSING                           │
│  Raw Documents (PDF, DOCX, Scans) ──> app/parser + app/normalizer ──> Canonical DOM      │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                2. ONTOLOGY-GUIDED KGC (app/kg/)                          │
│  Canonical DOM (Blocks, Tables, Reading Order) + Domain Ontology (TBox)                  │
│  ──> Two-Stage Hybrid Extractor (GLiNER + Structured LLM) ──> Knowledge Graph (ABox)     │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                3. GRAPH STRUCTURAL VALIDATION                            │
│  - SHACL / Pydantic Schema Validation (100% Domain/Range typing)                         │
│  - Contradiction & Conflict Resolution                                                   │
│  - Complete Lineage Verification (doc_id, block_id, page_index, cell_coord)              │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                4. TOPOLOGY SAMPLING (app/synthetic/sampler.py)           │
│  - Linear k-hop Chains (Deductive Multi-Hop Reasoning)                                   │
│  - Tree / Intersections (Multi-Constraint Synthesis)                                     │
│  - Table & Attribute Comparisons (Numerical Reasoning)                                   │
│  - Counterfactual Edge Inversions (Calibrated DPO Negative Pairs)                        │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                5. VERBALIZATION & SYNTHESIS (app/synthetic/generator.py) │
│  - Schema-Constrained Prompting with <think> Chain-of-Thought Formulation                │
│  - Natural Language Instruction Synthesis                                                │
│  - Verbatim DOM Block Context Ingestion                                                  │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                6. DUAL-GATE VERIFICATION (app/synthetic/validator.py)    │
│  Gate 1: Symbolic Graph Execution (SPARQL/Cypher query execution against KG)             │
│  Gate 2: Ragas Faithfulness & Intermediate CoT Grounding Audit                           │
│  Gate 3: De-duplication (Self-BLEU < 0.35) & Privacy / PHI Scrubbing                    │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                7. DATASET EXPORT & PACKAGING (app/synthetic/exporter.py) │
│  - ChatML / OpenAI JSONL (with <think> reasoning tokens)                                 │
│  - Alpaca Instruction Format                                                             │
│  - Direct Preference Optimization (DPO) Arrow Datasets (Chosen vs. Perturbed Rejected)   │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Knowledge Graph Blueprint (`app/kg/`)

### A. Source Data Mapping from Canonical DOM
The Knowledge Graph builds strictly upon the `Document` DOM models defined in `app/parser/dom/models.py`:
- `Document.document_id` & `source_hash` $\rightarrow$ Graph metadata & cryptographic proof.
- `Block.id`, `page`, `bbox`, and `text` $\rightarrow$ Edge provenance, spatial coordinates, and evidence spans.
- `Block.kind == "heading"` $\rightarrow$ Structural context nodes (scoping sections like *"Contraindications"*).
- `Table.header` & `Table.rows` $\rightarrow$ Relational property edges preserving cell geometry `(row, col)`.
- `Document.reading_order_full` $\rightarrow$ Traversal sequence resolving inter-paragraph coreferences.

### B. Ontology (TBox) Design
- **Hybrid Architecture:** Standardized base medical ontologies (UMLS, SNOMED-CT, RxNorm) encoded as Pydantic schemas, with dynamic LLM-guided emergent relation induction for novel concepts.
- **Strict Typing:** All relation predicates enforce explicit mathematical domain and range constraints.

### C. Extraction Engine
- **Stage 1 (Candidate Extraction):** High-speed local GLiNER transformer (30–60ms/page) to extract entity mentions without API costs.
- **Stage 2 (Relation Linking & Normalization):** Structured-output LLM (Instructor / Outlines) with Pydantic schemas to link entity pairs into directed edges with verbatim evidence quotes.

---

## 4. Why Use a Knowledge Graph? (The Critical Trade-Off)

| Dimension | Pure Text Chunking (e.g. meta-llama/synthetic-data-kit) | Knowledge-Graph Grounded (Our System) |
| :--- | :--- | :--- |
| **Multi-Hop Reasoning** | Stochastic; model guesses connections across text chunks; high rate of intermediate hallucinations. | **Deterministic;** exact $k$-hop paths ($e_1 \rightarrow e_2 \rightarrow e_3$) ensure mathematically valid multi-step logic. |
| **Ground Truth Truthfulness** | Relies on LLM-as-a-Judge (can be tricked by plausible-sounding text). | **Symbolic Execution;** queries executed against graph topology confirm exact answer uniqueness. |
| **Hard Negatives for DPO** | Random shuffle or vector-similarity distractors (often trivial or accidentally correct). | **Calibrated Edge Inversions;** precise semantic perturbations create high-difficulty preference pairs. |
| **Provenance & Auditability** | Raw file or chunk string. | **Exact Page & Bounding Box Coordinates;** interactive audit down to individual table cells. |

*When is KG NOT needed?* For simple single-sentence factoid QA or open-ended stylistic rewriting, Knowledge Graphs add unnecessary overhead and are bypassed in favor of direct DOM chunking.

---

## 5. Exhaustive 12-Dimensional Evaluation Framework

Every generated dataset is certified against 12 standardized evaluation dimensions:
1. **Fidelity:** Ragas Faithfulness Score $\ge 0.95$.
2. **Utility:** Downstream model accuracy improvement on gold benchmarks $\Delta \text{Acc} \ge +5.0\%$.
3. **Diversity:** Self-BLEU-4 $\le 0.35$; Vendi Score $> 15.0$.
4. **Coverage:** $\ge 80\%$ of active ontological relations represented.
5. **Correctness:** $100\%$ symbolic graph execution match against ground truth.
6. **Consistency:** Internal reasoning contradiction rate $< 0.5\%$.
7. **Privacy:** Zero unmasked Protected Health Information (PHI) / PII entities.
8. **Memorization:** Maximum verbatim token sequence length $< 15$ continuous tokens.
9. **Bias & Fairness:** Demographic Disparate Impact Ratio $\in [0.80, 1.25]$.
10. **Robustness:** Adversarial paraphrase prediction stability $\ge 90\%$.
11. **Reproducibility:** $100\%$ deterministic symbolic graph paths under fixed seeds.
12. **Cost & Efficiency:** Total compilation cost $\le \$0.015$ per verified reasoning pair.

---

## 6. Major Design Decisions & Justifications

1. **Modular Monolith Seam Integration:**
   * *Decision:* Implement `app/kg/` and `app/synthetic/` as distinct Python packages inside the existing modular monolith rather than spinning up external microservices.
   * *Why:* Preserves clean in-memory interfaces with `app/parser` and `app/chunking`, zero network serialization overhead, and simplified atomic testing.
2. **In-Memory NetworkX Graph Engine for MVP:**
   * *Decision:* Use an in-memory NetworkX / dictionary adjacency structure with JSON serialization rather than a Neo4j cluster.
   * *Why:* Handles up to 1,000,000 triples with zero infrastructure dependencies and sub-millisecond path traversals.
3. **Dual-Gate Verification Pipeline:**
   * *Decision:* Combine symbolic graph query validation (SPARQL/Cypher) with neural Ragas faithfulness auditing.
   * *Why:* Symbolic execution guarantees relational correctness; neural auditing guarantees natural language fluency and relevance.

---

## 7. Open Questions to Confirm Before Implementation

1. **Base Domain Ontology Selection:** Will the initial healthcare deployment use a standardized UMLS/SNOMED subset, or an enterprise-specific Pydantic schema provided by the client?
2. **Inference Backend for Local Extraction:** Should GLiNER and the small arbiter model run strictly on local CPU/GPU (preserving on-prem data isolation) or connect to private enterprise LLM endpoints?
3. **Target SLM Architectures:** Which downstream model architectures (e.g. Llama 3.1 8B, Qwen 2.5 7B, Mistral Nemo) will be the primary consumers of the exported datasets?
