# Architecture Blueprint & System Decision Record

This document establishes the system problem definition, the end-to-end lifecycle, error propagation chains, research gaps, architectural decisions, and recommended empirical experiments.

---

## Part 1: Problem Definition & Synthetic Record Contract

### A. What Exactly is Being Generated?
In this system (**MedFactory AI**), a synthetic dataset is **NOT** a collection of mock demographic rows or ungrounded creative text. It is a **Curated, Provenance-Anchored, Multi-Hop Reasoning & Instruction Corpus** derived directly from verified enterprise/clinical documents.

### B. The Canonical Synthetic Record Schema
Every generated synthetic record adheres to the following strict Pydantic contract:

```json
{
  "record_id": "syn-rec-20260921-004892",
  "dataset_version": "v1.0.0",
  "task_type": "multi_hop_reasoning",
  "domain": "healthcare",
  "instruction": "A 58-year-old patient with chronic kidney disease (Stage 4) presents with severe neuropathic pain. Analyze the contraindications of first-line neuropathic agents and recommend a dosage-adjusted regimen supported by the clinical guidelines.",
  "reasoning_trace": "<think>\n1. Identify first-line neuropathic pain agents: Pregabalin, Gabapentin, Duloxetine.\n2. Query contraindications for Stage 4 CKD (eGFR 15-29 mL/min): Gabapentin and Pregabalin undergo renal excretion and require mandatory dose reduction; Duloxetine should be avoided if eGFR < 30 mL/min due to drug accumulation.\n3. Verify evidence in DOM block [blk-004-012]: 'Pregabalin starting dose must not exceed 25 mg daily for eGFR < 30 mL/min.'\n4. Synthesize clinical recommendation with step-by-step pharmacokinetic justification.\n</think>",
  "target_response": "For a patient with Stage 4 CKD (eGFR 15-29 mL/min), Duloxetine is not recommended. Pregabalin is indicated but requires strict renal dose adjustment: initiate at 25 mg once daily...",
  "negative_response": "Duloxetine 60 mg daily is the preferred first-line treatment without dose adjustment.",
  "grounding_subgraph": {
    "nodes": ["Pregabalin", "Chronic Kidney Disease Stage 4", "Renal Clearance", "Duloxetine"],
    "edges": [
      {"subject": "Pregabalin", "predicate": "REQUIRES_DOSE_ADJUSTMENT", "object": "Chronic Kidney Disease Stage 4"},
      {"subject": "Duloxetine", "predicate": "CONTRAINDICATED_IN", "object": "eGFR < 30 mL/min"}
    ]
  },
  "provenance": [
    {
      "doc_id": "guideline-ckd-pain-2024",
      "page_index": 4,
      "block_id": "blk-004-012",
      "bbox": {"x0": 72.0, "y0": 210.5, "x1": 510.0, "y1": 280.0},
      "source_text": "Pregabalin starting dose must not exceed 25 mg daily for eGFR < 30 mL/min."
    }
  ],
  "quality_metadata": {
    "symbolic_execution_valid": true,
    "ragas_faithfulness_score": 0.98,
    "leakage_check_passed": true,
    "phi_free": true,
    "complexity_hops": 3
  }
}
```

### C. Downstream Consumption & Constraints
* **Intended Downstream Use:**
  1. **Supervised Fine-Tuning (SFT):** Training domain-specific SLMs (e.g. Llama 3.1 8B, Qwen 2.5 7B, BioMistral) on multi-hop clinical reasoning.
  2. **Direct Preference Optimization (DPO / RLHF):** Utilizing paired `target_response` (chosen) vs. `negative_response` (calibrated counterfactual perturbation) to eliminate hallucinations.
  3. **RAG & Agent Evaluation:** Ground-truth benchmarking of enterprise retrieval systems.
* **Invariant Properties (Must Never Change):** Clinical facts, drug interactions, contraindications, numerical dosage thresholds, and physical provenance pointers.
* **Allowed Permutations (Can Change):** Natural language phrasing, syntactic tone, patient personas (names, ages, clinical vignettes), and question formatting.

---

## Part 2: Complete System Lifecycle & Execution Boundaries

```
[ Stage 1: Ingestion & Parser ]
         │ (Input: Raw PDF/DOCX -> Output: Canonical DOM)
         ▼
[ Stage 2: Normalizer & Hygiene ]
         │ (Input: Raw DOM -> Output: Clean Normalized DOM)
         ▼
[ Stage 3: Knowledge Extraction & KGC ]
         │ (Input: Clean DOM + Ontology -> Output: Knowledge Graph + Lineage)
         ▼
[ Stage 4: Graph Structural Validation ]
         │ (Input: KG -> Output: Certified Graph Instance)
         ▼
[ Stage 5: Subgraph Topology Sampling ]
         │ (Input: Certified KG -> Output: Subgraph Walks & Motifs)
         ▼
[ Stage 6: Verbalization & Synthesis ]
         │ (Input: Subgraph + Prompt Templates -> Output: Synthetic QA + CoT)
         ▼
[ Stage 7: Dual-Gate Verification & Filtering ]
         │ (Input: Raw Synthetic Data -> Output: Validated Training Records)
         ▼
[ Stage 8: Exporter & Formats ]
         │ (Input: Validated Records -> Output: Alpaca, ChatML, Arrow, DPO)
         ▼
[ Stage 9: Downstream Model Evaluation ]
         │ (Input: Downstream Model -> Output: Benchmark Delta Score)
```

### Stage-by-Stage Operational Characteristics

| Stage | Input $\rightarrow$ Output | Mandatory? | Primary Failure Mode | Boundary Validation | Cost / 1k Pages |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Parser** | Binary File $\rightarrow$ Canonical DOM | **Mandatory** | OCR failure, table structure drop | Schema validation (`Document.validate()`) | Low ($<\$0.10$) |
| **2. Normalizer** | Canonical DOM $\rightarrow$ Clean DOM | **Mandatory** | Broken hyphenation, Unicode corruption | Normalization report checks | Negligible ($<\$0.01$) |
| **3. KG Extraction** | Clean DOM $\rightarrow$ Triples + Lineage | **Mandatory** | Extractor hallucination, missed entities | Pydantic TBox typing checks | Moderate ($\$1.00-\$3.00$) |
| **4. Graph Validation** | Raw Triples $\rightarrow$ Validated KG | **Mandatory** | Disconnected graphs, contradiction loops | SHACL / Cycle checks | Negligible ($<\$0.05$) |
| **5. Subgraph Sampling** | Validated KG $\rightarrow$ Subgraph Walks | **Mandatory** | Combinatorial explosion, trivial paths | Path entropy & degree filtering | Negligible ($<\$0.01$) |
| **6. Verbalization** | Subgraphs $\rightarrow$ Raw Synthetic QA | **Mandatory** | Premise leakage, unfaithful CoT | Prompt template validation | Moderate ($\$2.00-\$6.00$) |
| **7. Dual-Gate Filter** | Raw QA $\rightarrow$ Certified Dataset | **Mandatory** | False-positive LLM judge | Symbolic SPARQL re-execution | Low ($\$0.50-\$1.50$) |
| **8. Exporter** | Certified Data $\rightarrow$ SFT/DPO Datasets | **Mandatory** | Schema format errors (ChatML/JSONL) | Pydantic Export Schema checks | Negligible ($<\$0.01$) |
| **9. Model Evaluation** | Datasets $\rightarrow$ Downstream Metric | **Mandatory** | Test-set contamination | Leakage & Ragas benchmark run | Compute dependent |

---

## Part 3: Knowledge Graph Error Propagation Analysis

An error introduced in early stages propagates and amplifies downstream if not caught at boundary gates:

```
[ 1. Parser Error ] 
(e.g., Table column offset shifts dosage from 10mg to 100mg)
         │
         ▼
[ 2. DOM Error ] 
(Cell text carries corrupted 100mg value)
         │
         ▼
[ 3. KG Extraction Error ] 
(Edge created: (DrugA, RECOMMENDED_DOSE, 100mg))
         │
         ▼
[ 4. KG Reasoning Error ] 
(Graph path sampled containing lethal 100mg recommendation)
         │
         ▼
[ 5. Synthetic Data Error ] 
(LLM generates training pair teaching 100mg dose)
         │
         ▼
[ 6. Downstream Catastrophe ] 
(Fine-tuned medical SLM outputs lethal dosage recommendation)
```

### Boundary Defense Architecture
* **Parser $\rightarrow$ DOM Boundary:** Spatial bounding-box alignment check; OCR confidence threshold ($\ge 0.80$).
* **DOM $\rightarrow$ KG Boundary:** Strict Pydantic ontological typing; rejection of triples missing direct block text evidence.
* **KG $\rightarrow$ Synthesis Boundary:** Symbolic query validation; checking that intermediate entities do not create paradoxical contradiction cycles.
* **Synthesis $\rightarrow$ Export Boundary:** Ragas Faithfulness verification comparing generated CoT steps against source DOM verbatim text blocks.

---

## Part 4: Research Gaps & Knowledge Stratification

| Stratification Level | Research Finding / Concept | Evidence / Status | Practical Impact on Our Architecture |
| :--- | :--- | :--- | :--- |
| **1. Established Knowledge** | Schema-constrained LLM generation (Pydantic/Grammars) prevents syntactic hallucinations. | High (Text2KGBench, Outlines, Instructor) | **Adopt immediately** in `app/kg/schema.py`. |
| **1. Established Knowledge** | Multi-hop graph paths generate significantly more faithful CoT reasoning data than unstructured chunk prompting. | High (RoG, ToG, MindMap) | **Adopt immediately** in `app/synthetic/sampler.py`. |
| **2. Strong Evidence** | Hybrid layout parsing (combining text layout with visual table coordinates) improves relation extraction F1 by $>15\%$. | Strong (Doc2KG, Docling benchmarks) | **Adopt** via our existing Canonical DOM integration. |
| **3. Emerging Research** | Automated counterfactual edge perturbation for generating calibrated DPO negative pairs. | Emerging (Med-HALT 2024, GraphInstruct 2024) | **Implement as experimental feature** in Phase 2. |
| **4. Open Research Question** | How to automatically resolve ambiguous clinical abbreviations across massive cross-institutional document corpora without human review. | Unresolved (Active NLP research) | **Mitigate** via confidence gating and human-in-the-loop review queues. |
| **5. Our Inference** | KG provides zero value for single-sentence factoid extraction and should be bypassed in favor of direct DOM chunking for simple tasks. | Empirical Inference | **Adopt dual-route architecture** (Fast DOM route for simple QA; KG route for multi-hop CoT). |

---

## Part 5: Proposed Architectural Decisions

### Component-by-Component Specifications

#### Component 1: `app/kg/schema.py` (Domain Ontology & TBox)
* **Purpose:** Defines the domain ontology (Entity classes, relation predicates, and constraints) via Pydantic.
* **Input:** Domain ontology definitions (UMLS / SNOMED / Enterprise schemas).
* **Output:** Strongly-typed Pydantic classes and JSON-schema constraints.
* **Why Chosen:** Guarantees runtime determinism and prevents LLM relation hallucinations.
* **Alternatives:** OWL/RDF Protégé files (too complex/slow for Python runtime), Free-form strings (unreliable).

#### Component 2: `app/kg/extractor.py` (DOM-to-Graph Extractor)
* **Purpose:** Translates Canonical DOM blocks, headings, and tables into knowledge graph triples with complete provenance.
* **Input:** Canonical `Document` DOM (`app/parser/dom/models.py`).
* **Output:** Extracted entity nodes and relation edges with `block_id`, `page_index`, and `cell_coord` lineage.
* **Why Chosen:** Direct integration with our parser eliminates file re-reading and preserves layout context.
* **Alternatives:** Passing raw text files to LangChain/LlamaIndex (destroys DOM table structure and provenance).

#### Component 3: `app/kg/graph.py` (Knowledge Graph Store)
* **Purpose:** In-memory / NetworkX property graph engine for graph traversal, path finding, and query execution.
* **Input:** Extracted nodes and edges.
* **Output:** Connected graph index supporting $k$-hop path queries and SPARQL/Cypher style traversals.
* **Why Chosen:** Lightweight, zero external infrastructure overhead for modular monolith MVP.
* **Alternatives:** Neo4j / Memgraph cluster (unnecessary infrastructure overhead for single-node development).

#### Component 4: `app/synthetic/sampler.py` (Topology Sampler)
* **Purpose:** Samples distinct graph motifs (linear chains, tree intersections, comparative tables, counterfactuals).
* **Input:** Validated `KnowledgeGraph`.
* **Output:** List of `SubgraphPath` objects for verbalization.
* **Why Chosen:** Enables deterministic control over reasoning complexity.

#### Component 5: `app/synthetic/generator.py` (Verbalizer & CoT Synthesizer)
* **Purpose:** Converts subgraph paths into fluent natural language instructions, `<think>` reasoning traces, and ground-truth answers.
* **Input:** `SubgraphPath` + DOM evidence context.
* **Output:** Raw `SyntheticRecord` candidates.

#### Component 6: `app/synthetic/validator.py` (Dual-Gate Verification)
* **Purpose:** Validates synthetic candidates via symbolic graph execution and Ragas faithfulness auditing.
* **Input:** Raw `SyntheticRecord` candidates.
* **Output:** Certified `SyntheticRecord` dataset or failure logs.

#### Component 7: `app/synthetic/exporter.py` (Format Serialization)
* **Purpose:** Serializes certified datasets into Alpaca, ChatML/OpenAI JSONL, and Hugging Face Arrow formats.

### What Should NOT Be Built Yet (Insufficient Evidence / Over-Engineering)
1. **Distributed Neo4j Graph Database Cluster:** NetworkX / in-memory adjacency list is fully sufficient for corpora $<100,000$ documents and avoids complex database administration.
2. **Multi-Agent Conversational Debate Simulation:** Single-turn prompted verbalization with symbolic path constraints provides higher fidelity at $1/10\text{th}$ the compute cost.
3. **Continuous Real-Time Graph Streaming:** Batch execution is the optimal model for synthetic dataset compilation.

---

## Part 6: Recommended Empirical Experiments

To empirically validate the architecture before full production deployment, the following 4 experiments are designed:

### Experiment 1: KG-Grounded Multi-Hop Generation vs. Direct Chunk Prompting
* **Hypothesis:** KG-grounded multi-hop QA generation achieves $>95\%$ reasoning faithfulness compared to $<70\%$ for ungrounded chunk-based LLM generation.
* **Variables:** Independent = Generation Method (KG Subgraph vs. Sliding-Window Text Chunks); Dependent = Ragas Faithfulness, Intermediate Step Accuracy, Hallucination Rate.
* **Dataset:** 50 parsed clinical guideline documents from Corpus B.
* **Baseline:** `meta-llama/synthetic-data-kit` standard chunking prompt.
* **Metrics:** Faithfulness score, Symbolic graph execution accuracy.

### Experiment 2: Two-Stage Hybrid Entity Extraction vs. Pure LLM Extraction
* **Hypothesis:** GLiNER candidate extraction followed by structured Pydantic LLM disambiguation achieves $\ge 94\%$ F1 while reducing extraction cost by $>75\%$ compared to pure LLM extraction.
* **Variables:** Extractor engine (Pure GPT-4o vs. GLiNER + Small LLM arbiter).
* **Metrics:** Entity F1, Processing Latency (ms/page), API Cost ($/1k pages).

### Experiment 3: Table-to-Graph Extraction vs. Linear Text Flattening
* **Hypothesis:** Table-aware relational extraction preserving `Row` $\times$ `Cell` geometry yields $>90\%$ relational accuracy on tabular data compared to $<50\%$ for flattened Markdown text.
* **Variables:** Table representation (Canonical DOM Table Nodes vs. Flattened Markdown Strings).
* **Metrics:** Table Relation F1, Numerical Attribute Preservation Rate.

### Experiment 4: Calibrated DPO Perturbation vs. Random Negative Sampling
* **Hypothesis:** Fine-tuning an SLM using KG counterfactual edge-inversion negatives reduces downstream medical hallucination rates by $>30\%$ compared to random/BM25 distractor negatives.
* **Metrics:** Downstream Med-HALT Hallucination Score, MedQA Accuracy Delta.
