# Synthetic Data Generation Methodologies & Knowledge Graph Trade-Off Analysis

This document provides a systematic evaluation of synthetic data generation methodologies, an honest analysis of Knowledge Graphs versus alternatives, and the prerequisite matrix required before dataset synthesis.

---

## 1. Why Use a Knowledge Graph for Synthetic Data? (Critical Analysis)

The fundamental question must be addressed rigorously:
> **What does a Knowledge Graph provide for synthetic-data generation that a simpler document-grounded (pure RAG) or direct LLM-prompted pipeline cannot provide?**

```
                    ┌─────────────────────────────────────────────────────────┐
                    │               Raw Document Chunk / RAG                  │
                    │  - Unstructured text blob                               │
                    │  - Implicit relationships                               │
                    │  - Non-deterministic hop discovery                      │
                    │  - High risk of intermediate reasoning hallucination    │
                    └─────────────────────────────────────────────────────────┘
                                                 vs
                    ┌─────────────────────────────────────────────────────────┐
                    │          Structured Knowledge Graph Subgraph            │
                    │  - Explicit typed entities: (Drug) -> (Target) -> (Gene)│
                    │  - Deterministic multi-hop path: Hops = exactly 3       │
                    │  - Verifiable intermediate states                       │
                    │  - Mathematically provable ground-truth answer          │
                    └─────────────────────────────────────────────────────────┘
```

### A. Concrete Benefits Enabled by Knowledge Graphs

| Capability / Benefit | Mechanism in KG | Concrete Example in Our System | Why Pure Text / Prompting Fails |
| :--- | :--- | :--- | :--- |
| **1. Controlled Multi-Hop Reasoning** | Deterministic graph walks ($k$-hop traversal) | Walking $(E_1 \xrightarrow{r_1} E_2 \xrightarrow{r_2} E_3 \xrightarrow{r_3} E_4)$ to generate 3-hop clinical questions. | LLMs hallucinate false intermediary links or skip steps when trying to connect disparate text chunks. |
| **2. Relational Consistency** | Formal ontology constraints ($Domain/Range$) | Ensuring `CONTRAINDICATED_IN` only connects `Drug` $\rightarrow$ `Condition/PatientGroup`. | LLMs easily produce logically inconsistent statements like "Aspirin is contraindicated in Penicillin". |
| **3. Exact Constraint-Based Generation** | Graph topology filters (node degree, centrality) | Generating questions specifically targeting rare diseases (node degree $< 3$) or hub biomarkers (centrality $> 0.8$). | Unstructured text sampling generates disproportionately generic or common-case questions. |
| **4. Hard-Negative & Distractor Sampling** | Near-neighbor graph sampling | Picking a distractor drug $D_2$ that shares the same drug class as $D_1$ but lacks the specific binding target. | Random or vector-similarity negatives are either too obvious (trivial) or accidentally true (false negatives). |
| **5. Counterfactual & Contradiction Generation** | Controlled edge inversion / deletion | Inverting an edge from `INHIBITS` to `ACTIVATES` to create a calibrated premise-contradiction test sample for DPO. | Hallucinated prompt edits often break grammar or alter multiple unintended facts simultaneously. |
| **6. Compositional & Set-Intersection Generation** | DAG / Tree subgraphs: $\{E_1 \rightarrow X \land E_2 \rightarrow X\}$ | *"Identify all kinase inhibitors approved after 2020 that target EGFR T790M."* | Pure text retrieval struggles to find the exact intersection of multiple disparate document sections. |
| **7. Coverage & Distribution Balancing** | Stratified sampling across graph subgraphs | Measuring exactly what percentage of relations in the corpus have synthetic training examples. | Document chunk sampling over-samples repetitive intro paragraphs and under-samples dense tabular footnotes. |
| **8. Ground-Truth Attribution & Provenance** | Graph edge metadata pointers | Every intermediate reasoning step cites the exact `block_id`, `page`, and `bbox` from the Canonical DOM. | LLM-generated citations frequently reference hallucinated page numbers or phantom sections. |
| **9. Deterministic De-duplication** | Graph isomorphism / Jaccard similarity of path nodes | Identifying if two differently phrased questions rely on the exact same underlying sub-graph path. | Text embedding similarity cannot reliably distinguish paraphrased multi-hop paths from distinct facts. |
| **10. Long-Range Cross-Document Synthesis** | Transitive graph joins across independent files | Connecting clinical findings from Document A to biochemical mechanism in Document B via shared entity CUI. | Document chunks from separate files are rarely co-retrieved with full multi-step alignment. |

### B. Honest Assessment: Where Knowledge Graphs Provide Little or No Benefit

A Knowledge Graph adds substantial engineering complexity (ontology design, entity extraction, relation linking, graph indexing). In the following scenarios, **KGs provide little or no benefit and should NOT be used**:

1. **Pure Stylistic or Tone Transfer:** Generating formal clinical summaries from doctor notes (simple sequence-to-sequence rewriting).
2. **Single-Sentence / Short-Span Factoid QA:** If the question and answer exist in a single continuous sentence, standard document chunking (e.g. `meta-llama/synthetic-data-kit` approach) is $10\times$ faster and cheaper with identical accuracy.
3. **Open-Ended Creative Summaries:** High-level thematic executive summaries where structured relational precision is secondary to fluent prose synthesis.
4. **Unstructured Narrative Flow:** Generating natural conversational patient dialogues where clinical structure is minimal.

---

## 2. Comprehensive Comparison of Synthetic Data Generation Methods

We analyze 13 major synthetic data generation paradigms:

| Method | Prerequisite Data | Core Models / Tech | Cost & Latency | Controllability | Diversity | Fidelity | Privacy Guarantee | Appropriate Use Cases | Inappropriate Use Cases | Relevant to Our System? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Rule / Template-Based** | Handcrafted grammars, slot templates | Python string templates, Jinja2 | Negligible ($<\$0.001$, $<1$ms) | Perfect ($100\%$) | Very Low (repetitive) | High on slots, Low on prose | Absolute (no real data leakage) | Form validation, deterministic unit tests, slot-filling QA. | Complex reasoning, open-domain text, fluent clinical dialogue. | **Yes** (as prompt scaffold) |
| **2. Programmatic Generation** | Code logic, domain rules | Python generators, Faker, SymPy | Negligible ($<\$0.001$, $<1$ms) | Perfect ($100\%$) | Low-Medium | Perfect on logic | High | Mathematical reasoning, date/time logic, synthetic FHIR records. | Nuanced natural language instruction tuning. | **Yes** (for metadata & IDs) |
| **3. Simulation-Based** | Mechanistic state models | Agent-based simulators, differential equ. | High compute | High on rules | Medium | High for modeled physics | High | Epidemiological spread, clinical trial patient trajectory simulation. | Textual instruction datasets. | **No** (out of current scope) |
| **4. Statistical / Copula** | Tabular historical data | SDV, Gaussian Copula, CTGAN | Low | Medium | Medium | High for correlations | Moderate (needs DP) | Tabular healthcare records, patient demographics. | Unstructured documents, text generation. | **No** (tabular only) |
| **5. GAN-Based (e.g. SeqGAN)** | Large training text corpus | Discriminator + Generator NNs | High training cost | Poor (mode collapse) | Low | Low-Moderate | Moderate | Image synthesis, legacy tabular synthesis. | Multi-hop reasoning, long-form structured text. | **No** (obsolete for text) |
| **6. VAE-Based** | Unlabeled text / latent space | Variational Autoencoders | Moderate | Moderate | Moderate | Moderate | Moderate | Latent space exploration, molecular design. | Multi-step reasoning instruction tuning. | **No** |
| **7. Diffusion-Based (Text)** | Text corpora | Text diffusion models | High latency | Medium | High | Moderate-High | Moderate | Sequence generation, continuous embedding synthesis. | Verifiable relational reasoning data. | **No** |
| **8. Direct LLM Prompting** | Seed instructions, system prompts | Frontier LLMs (Claude, GPT-4, Llama 3) | High ($\$5-\$20$/1k) | Low-Medium | Very High | Low-Moderate (hallucinations) | Poor (parametric leakage) | Brainstorming, general conversation, open-domain QA. | Regulated healthcare, verifiable multi-hop reasoning. | **No** (as standalone) |
| **9. Retrieval-Grounded (RAG)** | Chunked documents + Vector index | Vector Store (BGE-M3) + LLM | Moderate ($\$2-\$8$/1k) | Medium | High | Moderate (chunk boundary loss) | High (grounded) | Single-chunk QA, localized summarization. | Complex multi-hop reasoning across 3+ documents. | **Yes** (as baseline) |
| **10. Knowledge Graph (KG) Grounded** | Parsed DOM + Graph Triples + Ontology | Graph Store + Path Walkers + LLM | Moderate ($\$2-\$6$/1k) | Very High | High | Very High (verifiable) | Very High (provenance traced) | **Multi-hop reasoning, clinical CoT, DPO preference pairs, graph QA.** | Casual chat, simple open-ended summaries. | **YES (CORE SEAM)** |
| **11. Database / Query-Driven** | Structured SQL / SPARQL DB | SPARQL $\rightarrow$ NL Verbalizer | Low | High | Medium | Perfect on facts | High | Text-to-SQL, Text-to-SPARQL training pairs. | Rich unstructured contextual synthesis. | **Yes** (for validation) |
| **12. Multi-Agent Iterative (Self-Play)** | Persona specs, debate rules | Multi-agent frameworks (AutoGen, CrewAI) | Very High | Medium | Very High | Moderate-High | Moderate | Complex multi-turn negotiation, agent tool-calling datasets. | High-throughput batch dataset production. | **Optional** (future) |
| **13. Hybrid KG-DOM Augmented** | Canonical DOM + Graph Triples + Vector Seam | DOM Parser + KG + Teacher LLM + Judge | Moderate-High | **Maximum** | **Maximum** | **Maximum (SOTA)** | **Maximum** | **Enterprise synthetic reasoning, verifiable fine-tuning corpora.** | Trivial single-sentence tasks. | **YES (TARGET ARCHITECTURE)** |

---

## 3. Synthetic Data Generation Prerequisites Matrix

Before launching a production synthetic dataset generation run, the following prerequisites are categorized:

```
[ Mandatory Prerequisites ] ──> MUST exist before any generation call executes.
[ Useful Prerequisites ]    ──> Significantly improves quality and diversity.
[ Optional / Advanced ]     ──> Needed only for specialized downstream modalities.
```

### A. Mandatory Prerequisites (Must Have)
1. **Normalized Canonical DOM (`app/parser` + `app/normalizer`):** Clean text, bounding boxes, table structures, and reading order sequence.
2. **Defined Domain Ontology (`app/kg/schema.py`):** Explicit Pydantic schemas defining valid entity classes, relation predicates, and domain/range rules.
3. **Extracted & Validated Knowledge Graph (`app/kg/`):** Graph triples with $100\%$ complete provenance back to `doc_id` and `block_id`.
4. **Deterministic Subgraph Sampler (`app/synthetic/sampler.py`):** Path generation algorithms for linear chains ($k$-hop), intersections, and tables.
5. **Verifiable Prompt Templates (`app/synthetic/generator.py`):** Schema-constrained prompt verbalizers with exact `<think>` CoT schemas.
6. **Symbolic Verification Engine (`app/synthetic/validator.py`):** Automated checker ensuring the question uniquely and deterministically resolves to the target answer node.

### B. Useful Prerequisites (High Value)
1. **Vector Embedding Sidecar (`app/embedding/` / BGE-M3):** Enables near-neighbor distractor sampling for high-quality DPO negative selection.
2. **Community Summaries (GraphRAG Leiden clusters):** Provides global contextual anchors when synthesizing cross-document themes.
3. **Lexical & Semantic Deduplication Index:** SimHash / MinHash index to block duplicate synthetic samples before storage.

### C. Optional / Method-Specific Prerequisites
1. **Differential Privacy Noise Layer:** Required only if synthetic datasets are shared externally with third-party untrusted entities.
2. **Synthetic FHIR Generator:** Required only for structured electronic health record (EHR) tabular synthesis.
3. **Human-in-the-Loop Review Queue:** Web dashboard for clinician/domain expert spot-checking of edge cases.
