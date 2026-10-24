# Architecture Decision Document: Knowledge Graph & Ontology-Grounded Synthetic Data Generation
## Three Production Architectures — Comparative Trade-Off Review

**Run ID:** `run-2026-10-21-synthetic-kg-architecture`  
**Gate:** 2 — Chief Architect  
**Status:** For User Review & Approval  
**Prerequisite:** Gate 1 Research (`research.md`) — COMPLETE  

---

## 0. Design Principles (Non-Negotiable Guardrails)

Before comparing architectures, we anchor to the guardrails the codebase already enforces:

1. **Modular Monolith:** No microservices without proof. Modules = responsibilities, not network boundaries.
2. **Append-Only Storage:** Versioned, content-addressed, immutable artifacts ($dom-v{n}.docJSON$, $kg-v{n}.json$).
3. **Trust & Provenance is the Product:** Every synthetic fact must trace back to $(\text{doc\_id}, \text{page}, \text{block\_id}, \text{bbox})$.
4. **Parser Independence:** The Canonical DOM (`app/parser/dom/models.py`) is the single source of truth. Nothing downstream touches raw files.
5. **Hardware Respect:** Development envelope is 16.5 GB RAM / 4 GB VRAM / ~85% disk(40+GB left). Sequential GPU resource locks (`--heavy-concurrency 1`).
6. **Production Scalability:** Must scale to distributed multi-GPU clusters without architectural rewrites.

---

## 1. Architecture Alpha: "Lean Graph" (Embedded Kùzu + Two-Stage Extractor)

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                ARCHITECTURE ALPHA                                      │
│                          "Lean Graph" — Embedded Everything                             │
└────────────────────────────────────────────────────────────────────────────────────────┘

[ Canonical DOM ]
       │
       ▼
┌──────────────────────────────┐
│ app/kg/ontology/             │  ← Pydantic TBox schemas (UMLS / SNOMED subset)
│   registry.py                │     OntologyProvider protocol
│   healthcare.py              │     Emergent relation flagging
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ app/kg/extractor/            │  ← Two-Stage Hybrid Pipeline
│   mentions.py   (GLiNER)    │     Stage 1: Local zero-shot NER (40ms/page)
│   relations.py  (LLM)       │     Stage 2: Structured-output LLM (Pydantic schemas)
│   tables.py     (Rule-based)│     Table header→predicate, row→subject, cell→object
│   coreference.py             │     Reading-order-aware pronoun resolution
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ app/kg/store/                │  ← Kùzu Embedded Property Graph
│   kuzu_store.py              │     Cypher queries, zero-copy PyArrow, disk-backed
│   protocol.py  (GraphStore)  │     Abstract protocol for future swap (Neo4j/etc)
│   validation.py              │     SHACL-equivalent Pydantic constraint checks
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ app/synthetic/               │  ← Synthesis Engine
│   sampler.py                 │     Cypher-based k-hop path + topology sampling
│   recipes/                   │     Pluggable Recipe registry
│     multi_hop.py             │     Linear chains (2-4 hops)
│     dpo_perturbation.py      │     Edge inversion → chosen/rejected pairs
│     table_reasoning.py       │     Row×Column aggregation QA
│   generator.py               │     Prompt templates + <think> CoT verbalization
│   validator.py               │     Dual-Gate: Symbolic Cypher re-execution + Ragas
│   exporter.py                │     ChatML, Alpaca, DPO Arrow, HF Parquet
│   cli.py                     │     python -m app.synthetic.cli generate ...
└──────────────────────────────┘
```

### Trade-Off Matrix

| Dimension | Rating | Detail |
| :--- | :---: | :--- |
| **Extensibility** | ★★★★★ | Every seam is a Python Protocol: `OntologyProvider`, `EntityExtractor`, `RelationExtractor`, `GraphStore`, `SynthesisRecipe`. New domains (finance, legal) = new provider class, zero core changes. |
| **Dev Hardware Fit** | ★★★★★ | Kùzu embedded = zero daemon, disk-backed columnar storage, <500 MB RSS for 1M triples. GLiNER runs on CPU. LLM calls batched sequentially. |
| **Query Expressiveness** | ★★★★☆ | Full OpenCypher: `MATCH path = (a)-[*2..4]->(b) RETURN path` for k-hop sampling. Recursive path queries built-in. No SPARQL (trade-off accepted). |
| **Prod Scalability** | ★★★★☆ | Kùzu scales to 100M+ triples on single node. For distributed: swap `GraphStore` protocol impl to Neo4j Bolt adapter. No code changes above the protocol. |
| **Provenance Integrity** | ★★★★★ | Every Kùzu relationship stores `doc_id`, `block_id`, `page_index`, `bbox` as first-class edge properties. Cypher queries filter by provenance natively. |
| **Privacy Integration** | ★★★☆☆ | No built-in DP. Privacy = post-generation gate (PHI scrubbing + memorization LCS check). Google DP-inference can be added as an optional `DPDraftVerifier` recipe. |
| **Operational Complexity** | ★★★★★ | `pip install kuzu` — embedded library, no JVM, no daemon, no cluster config. Same simplicity as DuckDB. |
| **Cost (API)** | ★★★★☆ | GLiNER local = free. Structured LLM calls only for ambiguous spans (~20% of blocks). Estimated $0.30–$1.00 per 1K pages for extraction; $2–$6 for synthesis. |
| **Multi-Hop Reasoning Quality** | ★★★★★ | Deterministic Cypher path walks guarantee exact hop count. Symbolic re-execution validates answers. |
| **Technical Debt Risk** | ★★★★★ | Clean Architecture: protocols at every boundary. Kùzu is MIT-licensed, actively maintained (v0.8+), and backed by the University of Waterloo database group. |

### When to Choose Alpha
- **You want maximum extensibility with minimum infrastructure.**
- **You want sub-millisecond graph queries without running a database server.**
- **Your corpus is <100K documents / <10M triples (covers 99% of enterprise use cases).**

---

## 2. Architecture Beta: "Full-Stack GraphRAG" (Neo4j + Microsoft GraphRAG + Leiden Communities)

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                ARCHITECTURE BETA                                       │
│                  "Full-Stack GraphRAG" — Neo4j + Community Summaries                   │
└────────────────────────────────────────────────────────────────────────────────────────┘

[ Canonical DOM ]
       │
       ▼
┌──────────────────────────────┐
│ app/kg/extractor/            │  ← Microsoft GraphRAG-style extraction
│   entity_claim.py   (LLM)   │     Full LLM extraction (ALL blocks, no GLiNER tier)
│   community.py               │     Leiden hierarchical community detection
│   summarizer.py              │     LLM community summaries at multiple granularities
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ Neo4j Server (External)      │  ← Client-server graph database
│   Cypher + APOC procedures   │     Full-text index, vector index, graph algorithms
│   Bolt Protocol adapter      │     Network serialization, connection pooling
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ app/synthetic/               │  ← Community-Aware Synthesis
│   global_sampler.py          │     Cross-community thematic synthesis
│   local_sampler.py           │     Entity-neighborhood detailed QA
│   hybrid_retrieval.py        │     Vector + Graph traversal (RRF ranking)
│   generator.py               │
│   validator.py               │
└──────────────────────────────┘
```

### Trade-Off Matrix

| Dimension | Rating | Detail |
| :--- | :---: | :--- |
| **Extensibility** | ★★★★☆ | Neo4j plugin ecosystem (APOC, GDS) is extensive. Adding new analytics = install plugin. But: tight coupling to Neo4j query dialect and operational model. |
| **Dev Hardware Fit** | ★★☆☆☆ | Neo4j JVM requires 4+ GB heap baseline. On the 16.5 GB dev box, this leaves <12 GB for parser + GLiNER + LLM. **Violates hardware constraint.** |
| **Query Expressiveness** | ★★★★★ | Full Cypher + APOC + Graph Data Science (PageRank, Louvain, node2vec). Most powerful query engine available. |
| **Prod Scalability** | ★★★★★ | Neo4j Aura / Enterprise clusters scale to billions of triples with replication, sharding, and managed backup. |
| **Provenance Integrity** | ★★★★☆ | Edge properties carry provenance. But: community summaries are LLM-generated abstractions that may lose fine-grained block-level provenance. |
| **Privacy Integration** | ★★★☆☆ | Same post-generation gate model. No built-in DP. |
| **Operational Complexity** | ★★☆☆☆ | Requires running and managing a JVM database server, monitoring heap, configuring connection pools, managing schema migrations, and backup/restore procedures. |
| **Cost (API)** | ★★☆☆☆ | ALL blocks processed by LLM for extraction (no GLiNER fast path). Community summarization requires additional LLM calls per cluster. Estimated $8–$15 per 1K pages for extraction; $3–$8 for synthesis. **3–5× more expensive than Alpha.** |
| **Multi-Hop Reasoning Quality** | ★★★★☆ | Community summaries enable global thematic synthesis (e.g., "What are the top 5 drug interaction themes across 1000 guidelines?"). But: local multi-hop precision is identical to Alpha. |
| **Technical Debt Risk** | ★★★☆☆ | External database dependency. Version pinning, driver compatibility, schema migration tooling, and JVM tuning expertise required. |

### When to Choose Beta
- **Your corpus exceeds 100K documents and you need distributed graph analytics (PageRank, community detection, node2vec).**
- **You need global corpus-wide thematic synthesis (not just per-document multi-hop).**
- **Your production team has dedicated database administrators.**
- **You can afford 3–5× higher LLM API costs.**

---

## 3. Architecture Gamma: "Dual-Route Adaptive" (Kùzu + DOM Direct + GRA Quality Triad)

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                ARCHITECTURE GAMMA                                      │
│            "Dual-Route Adaptive" — Best of Both + GRA Quality Peer Review              │
└────────────────────────────────────────────────────────────────────────────────────────┘

[ Canonical DOM ]
       │
       ├───────────────────────────────────────────────┐
       ▼                                               ▼
┌─────────────────────┐                    ┌─────────────────────────┐
│ ROUTE A: KG Path    │                    │ ROUTE B: DOM Direct     │
│ (Multi-Hop / DPO)   │                    │ (Single-Hop / Factoid)  │
│                     │                    │                         │
│ GLiNER + LLM →      │                    │ Chunk Retrieval →       │
│ Kùzu Graph →        │                    │ BGE-M3 Embedding →     │
│ Cypher k-hop walk→  │                    │ Direct LLM QA gen →    │
│ CoT Synthesis       │                    │ Simple Faithfulness     │
└─────────┬───────────┘                    └─────────┬───────────────┘
          │                                          │
          └──────────────┬───────────────────────────┘
                         ▼
          ┌──────────────────────────────┐
          │ GRA Quality Triad            │  ← Generator-Reviewer-Adjudicator
          │                              │
          │  Generator: Primary LLM      │     Produces candidate QA/CoT
          │  Reviewer:  Critic LLM       │     Checks factual grounding, clarity
          │  Adjudicator: Tie-breaker    │     Resolves scoring disagreements
          └──────────────────────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │ Trust & Verification Engine  │
          │                              │
          │  Gate 1: Symbolic Execution  │     Cypher path query = target answer?
          │  Gate 2: Provenance Audit    │     block_id, bbox, evidence_text present?
          │  Gate 3: PHI / PII Scrub     │     Zero unmasked identifiers?
          │  Gate 4: Ragas Faithfulness  │     Score ≥ 0.95 against DOM blocks?
          │  Gate 5: De-duplication      │     Self-BLEU ≤ 0.35?
          │  Gate 6: (Optional) DP Gate  │     ε-DP public-drafter verification?
          └──────────────────────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │ Recipe-Based Export          │
          │  ChatML / Alpaca / DPO Arrow │
          │  HF Parquet / Custom JSONL   │
          └──────────────────────────────┘
```

### Dual-Route Decision Logic

```python
def route_block(block: Block, graph: KnowledgeGraph) -> Route:
    """Decide whether a DOM block/section needs KG-grounded multi-hop
    synthesis or can be handled by simpler direct-DOM generation."""
    
    entity_count = graph.count_entities_in_block(block.id)
    relation_count = graph.count_relations_from_block(block.id)
    
    if relation_count >= 2 or entity_count >= 3:
        return Route.KG_MULTI_HOP      # Complex: needs graph reasoning
    elif block.kind == "table" or entity_count >= 1:
        return Route.KG_SINGLE_HOP     # Structured: KG verifiable but simple
    else:
        return Route.DOM_DIRECT         # Narrative: direct chunk QA sufficient
```

### Trade-Off Matrix

| Dimension | Rating | Detail |
| :--- | :---: | :--- |
| **Extensibility** | ★★★★★ | All Alpha protocols + Route selection logic as a pluggable `RoutingPolicy`. New generation strategies = new Recipe. New verification methods = new Gate. New export formats = new Exporter. |
| **Dev Hardware Fit** | ★★★★★ | Same as Alpha (Kùzu embedded). DOM Direct route skips graph entirely for simple blocks, saving ~40% extraction compute. |
| **Query Expressiveness** | ★★★★☆ | Full OpenCypher via Kùzu for Route A. DOM Direct route uses existing `app/chunking/` + `app/embedding/` seams (no graph queries needed). |
| **Prod Scalability** | ★★★★★ | Route A scales via GraphStore protocol swap. Route B scales via existing batch executor (`app/processing/`). Routes are independent — can be parallelized on different worker pools. |
| **Provenance Integrity** | ★★★★★ | Both routes produce provenance. Route A: full graph edge lineage. Route B: chunk_id → block_id → doc_id lineage (already built in `app/chunking/`). |
| **Privacy Integration** | ★★★★☆ | Gate 6 (optional): Google's inference-time DP public-drafter can be wired as a pluggable verification step in the Trust Engine. Not mandatory for v1. |
| **Operational Complexity** | ★★★★★ | Same as Alpha + GRA triad is 3 lightweight LLM calls (can use local Qwen-2.5-7B or Gemini Flash Lite). No external infrastructure. |
| **Cost (API)** | ★★★★★ | **Lowest cost of all three.** Route B (DOM Direct) for ~40% of blocks avoids extraction + graph entirely. GRA triad with small local models costs ~$0.05/1K for review. Overall: **$0.20–$0.80/1K pages for extraction; $1.50–$4.00 for synthesis.** |
| **Multi-Hop Reasoning Quality** | ★★★★★ | Route A provides the same deterministic Cypher walks as Alpha. Route B provides simpler but still grounded factoid QA. GRA triad catches quality issues that a single LLM judge misses. |
| **Technical Debt Risk** | ★★★★★ | Clean dual-route design prevents over-engineering: simple content gets simple processing. The GRA triad replaces fragile single-model LLM-as-Judge with a robust peer-review system. |
| **Global Corpus Synthesis** | ★★★☆☆ | Cross-document thematic synthesis requires transitive entity joins across Kùzu graphs (possible but not as ergonomic as Neo4j community detection). Mitigated by pre-computing cross-doc entity clusters during extraction. |

### When to Choose Gamma
- **You want the best cost-to-quality ratio.**
- **You want clean separation between "simple" and "complex" content processing.**
- **You want a GRA-style multi-agent quality triad instead of a fragile single LLM judge.**
- **You need to start small (dev hardware) but scale without rewrites.**

---

## 4. Head-to-Head Comparative Summary

| Metric | Alpha (Lean Graph) | Beta (Full-Stack GraphRAG) | Gamma (Dual-Route Adaptive) |
| :--- | :---: | :---: | :---: |
| **Dev Hardware Viable** | ✅ Yes | ❌ No (Neo4j JVM) | ✅ Yes |
| **Operational Overhead** | Minimal | High (JVM + drivers) | Minimal |
| **Extraction Cost / 1K Pages** | $0.30–$1.00 | $8.00–$15.00 | **$0.20–$0.80** |
| **Synthesis Cost / 1K Pages** | $2.00–$6.00 | $3.00–$8.00 | **$1.50–$4.00** |
| **Multi-Hop Quality** | ★★★★★ | ★★★★☆ | ★★★★★ |
| **Global Thematic Synthesis** | ★★☆☆☆ | ★★★★★ | ★★★☆☆ |
| **Quality Assurance Method** | Dual-Gate (Symbolic + Ragas) | Dual-Gate (Symbolic + Ragas) | **Triple-Gate (GRA Triad + Symbolic + Ragas)** |
| **Privacy Integration** | Post-Gen Scrubbing | Post-Gen Scrubbing | **Optional DP Gate (Pluggable)** |
| **Protocol Extensibility** | ★★★★★ | ★★★★☆ | ★★★★★ |
| **Time to First Working Demo** | 3–4 weeks | 6–8 weeks | **3–4 weeks** |
| **Future Neo4j Migration** | `GraphStore` swap | Already Neo4j | `GraphStore` swap |

---

## 5. Module Layout for Architecture Gamma (Recommended)

```
app/
├── parser/               # ✅ EXISTING: Canonical DOM Extraction Pipeline
├── normalizer/           # ✅ EXISTING: DOM Cleaning & Hygiene
├── chunking/             # ✅ EXISTING: Content-Addressed Semantic Chunks
├── embedding/            # ✅ EXISTING: BGE-M3 1024-dim Vector Seam
├── routing/              # ✅ EXISTING: Intelligent Document Router
├── processing/           # ✅ EXISTING: Batch Executor
│
├── kg/                   # 🆕 MODULE: Knowledge Graph Construction
│   ├── __init__.py
│   ├── ontology/         # TBox: Domain ontology definitions
│   │   ├── __init__.py
│   │   ├── protocol.py       # OntologyProvider abstract protocol
│   │   ├── healthcare.py     # Healthcare TBox (UMLS/SNOMED/RxNorm subset)
│   │   └── registry.py       # Multi-domain ontology registry
│   ├── extractor/        # DOM → Triple extraction
│   │   ├── __init__.py
│   │   ├── protocol.py       # EntityExtractor & RelationExtractor protocols
│   │   ├── mentions.py       # Stage 1: GLiNER / Dictionary / Regex candidates
│   │   ├── relations.py      # Stage 2: Structured-output LLM relation resolution
│   │   ├── tables.py         # Table → Relational triple extraction (rule-based)
│   │   └── coreference.py    # Cross-block coreference resolution
│   ├── store/            # Graph persistence & query
│   │   ├── __init__.py
│   │   ├── protocol.py       # GraphStore abstract protocol
│   │   ├── kuzu_store.py     # Kùzu embedded graph (MVP + production)
│   │   ├── memory_store.py   # In-memory dict fallback (tests, no Kùzu dep)
│   │   └── serializer.py     # Content-addressed JSON (kg-v{n}.json)
│   └── validation.py     # Ontological constraint checks, contradiction detection
│
├── synthetic/            # 🆕 MODULE: Synthetic Data Generation Engine
│   ├── __init__.py
│   ├── router.py         # Dual-Route decision logic (KG vs DOM Direct)
│   ├── sampler.py        # Cypher-based subgraph topology sampling
│   ├── recipes/          # Pluggable synthesis recipe registry
│   │   ├── __init__.py
│   │   ├── protocol.py       # SynthesisRecipe abstract protocol
│   │   ├── multi_hop.py      # Linear k-hop chain CoT generation
│   │   ├── dpo_perturbation.py # Counterfactual edge inversion → DPO pairs
│   │   ├── table_reasoning.py  # Tabular aggregation & comparison QA
│   │   ├── dom_direct.py      # Simple chunk-grounded factoid QA (Route B)
│   │   └── contradiction.py   # Adversarial premise-contradiction datasets
│   ├── quality/          # GRA Quality Triad
│   │   ├── __init__.py
│   │   ├── generator_agent.py   # Primary candidate generator
│   │   ├── reviewer_agent.py    # Independent quality critic
│   │   └── adjudicator_agent.py # Tie-breaking arbiter
│   ├── trust/            # Trust & Verification Engine
│   │   ├── __init__.py
│   │   ├── symbolic.py        # Gate 1: Cypher re-execution validator
│   │   ├── provenance.py      # Gate 2: Lineage completeness auditor
│   │   ├── privacy.py         # Gate 3: PHI/PII scrubber (Presidio)
│   │   ├── faithfulness.py    # Gate 4: Ragas faithfulness scorer
│   │   ├── dedup.py           # Gate 5: Self-BLEU / SimHash de-duplicator
│   │   └── dp_gate.py         # Gate 6: Optional DP public-drafter verifier
│   ├── exporter/         # Dataset format serialization
│   │   ├── __init__.py
│   │   ├── protocol.py        # ExportFormat abstract protocol
│   │   ├── chatml.py          # ChatML / OpenAI JSONL (with <think>)
│   │   ├── alpaca.py          # Alpaca instruction format
│   │   ├── dpo_arrow.py       # DPO (chosen/rejected) Arrow datasets
│   │   └── huggingface.py     # HuggingFace Parquet / Arrow
│   ├── schema.py         # Canonical SyntheticRecord Pydantic models
│   └── cli.py            # CLI entry point
│
└── evaluation/           # 🆕 MODULE: 12-Dimensional Evaluation Suite
    ├── __init__.py
    ├── suite.py           # Orchestrator running all 12 metrics
    ├── fidelity.py        # Ragas Faithfulness
    ├── diversity.py       # Self-BLEU & Vendi Score
    ├── coverage.py        # Ontological edge coverage
    ├── correctness.py     # Symbolic execution match
    ├── consistency.py     # NLI contradiction detection
    ├── privacy.py         # PHI/PII detection
    ├── memorization.py    # LCS verbatim span detection
    └── cost.py            # Per-record token & cost tracking
```

---

## 6. Key Design Decisions & Justifications

### Decision 1: Kùzu over Neo4j / NetworkX / RDFLib

| Factor | Kùzu (Chosen) | Neo4j | NetworkX | RDFLib |
| :--- | :--- | :--- | :--- | :--- |
| **Deployment** | `pip install kuzu` (embedded) | External JVM server | Pure Python | Pure Python |
| **RAM for 1M triples** | ~400 MB (disk-backed) | ~4 GB (JVM heap) | ~8 GB (Python dicts) | ~2 GB (indices) |
| **k-hop Path Query** | <0.5ms (C++ vectorized) | 2–8ms (network + JVM) | 5–20ms (Python loops) | 10–40ms (SPARQL) |
| **Python Integration** | Zero-copy PyArrow | Bolt driver (serialization) | Native | Native |
| **Schema Enforcement** | NodeTable/RelTable DDL | Labels + constraints | None | OWL/SHACL |
| **License** | MIT | Commercial/Community | BSD | BSD |

**Rationale:** Kùzu gives us the query power of Neo4j (full OpenCypher) with the deployment simplicity of DuckDB (embedded pip install, zero daemon). On our 16.5 GB dev box, Neo4j's 4 GB JVM heap would leave insufficient RAM for the parser + extractor pipeline. NetworkX fails at scale (>100K nodes = OOM). RDFLib's SPARQL is 20× slower than Cypher for path queries.

### Decision 2: Two-Stage Hybrid Extraction over Full-LLM / Full-Rule

**Stage 1 (GLiNER — Local, Fast):** Runs on CPU at 40ms/page. Extracts all candidate entity mentions with type labels. Zero API cost. Deterministic.

**Stage 2 (Structured LLM — Targeted, Precise):** Only invoked for blocks where Stage 1 finds ambiguous spans, nested entities, or complex relations. Uses Pydantic schema-constrained generation via Instructor/Outlines. ~20% of blocks need this, cutting LLM costs by 80%.

**Alternative rejected (Full LLM for all blocks):** $8–$15/1K pages vs $0.30–$1.00/1K pages. Microsoft SynthLLM and GraphRAG both use full-LLM extraction because they target internet-scale corpora; our enterprise corpus is smaller but requires higher precision and lower cost per document.

### Decision 3: GRA Quality Triad over Single LLM Judge

**Problem with single LLM-as-Judge:** Published research (Zheng et al., 2023, "Judging LLM-as-a-Judge") shows single-model judges exhibit position bias, verbosity bias, and self-enhancement bias. A single judge fails to catch ~15–30% of factual errors.

**GRA Triad (Generator → Reviewer → Adjudicator):** Multiple independent small models in a peer-review loop catch errors that any single model misses. The Adjudicator resolves conflicting judgments, preventing "majority misjudgment." The 36kr/GRA paper demonstrated that 7B-model triads match 72B-model single-judge quality.

**Cost:** 3 × small model calls (~$0.05/1K records total) vs 1 × frontier model call (~$0.10/1K records). Cheaper AND more robust.

### Decision 4: Dual-Route Architecture (KG + DOM Direct)

**Observation:** Not all content benefits from KG extraction. Simple narrative paragraphs without clinical entities, numerical data, or multi-hop relationships gain ZERO value from graph traversal. Forcing them through the KG pipeline wastes extraction compute and graph storage.

**Route A (KG Multi-Hop):** Blocks with ≥2 relations or ≥3 entities → Full KG extraction → Cypher path walks → CoT synthesis.
**Route B (DOM Direct):** Blocks with ≤1 entity and zero relations → Direct chunk retrieval → Simple QA generation via existing `app/chunking/` + `app/embedding/` seams.

**Estimated Split:** ~60% Route A / ~40% Route B, saving ~35% of total extraction compute.

### Decision 5: Content-Addressed Versioned Graph Storage

Mirroring the established patterns in `app/parser/storage.py` (ADR-008):

```
graphs/
  {doc_id}/
    kg-v{version}.json         # Serialized graph triples + provenance
    kg-summary-v{version}.json  # Optional: entity/relation statistics
  index/
    entity-index-v{version}.json  # Cross-document entity registry
```

Same-version writes are deterministic overwrites; prior versions are retained, never destroyed (append-only storage).

---

## 7. What Should NOT Be Built for v1

| Component | Why Not Now | When to Build |
| :--- | :--- | :--- |
| **Neo4j / Memgraph Cluster** | Kùzu embedded handles <10M triples. No infra overhead. | When corpus exceeds 100K docs or distributed analytics (PageRank, community) are required. |
| **Leiden Community Detection** | Requires graph-level analytics not in scope for per-document synthesis. | When global corpus-wide thematic synthesis is a product requirement. |
| **DP Public-Drafter Gate** | Privacy via post-gen PHI scrubbing is sufficient for internal use. Formal DP is a regulatory requirement, not a v1 feature. | When synthetic datasets are shared with external third parties under HIPAA Expert Determination. |
| **Multi-Agent Debate Simulation** | GRA triad (3 calls) provides sufficient quality. Full multi-turn debate is 10× more expensive. | When DPO preference pairs require adversarial stress-testing beyond counterfactual edge inversion. |
| **Real-Time Streaming KG Updates** | Batch is the natural execution model for document-to-synthetic pipeline. | When the platform serves live clinical decision support (not dataset generation). |

---

## 8. Recommended Architecture

**Architecture Gamma ("Dual-Route Adaptive")** is recommended because:

1. **Lowest cost** ($0.20–$0.80/1K pages extraction, $1.50–$4.00/1K pages synthesis) — 3–10× cheaper than Beta.
2. **Fits dev hardware** (16.5 GB RAM / 4 GB VRAM) — unlike Beta which requires external Neo4j.
3. **Maximum extensibility** — every seam is a Python Protocol; new domains, extraction methods, graph backends, recipes, and export formats are pluggable without touching core code.
4. **Strongest quality assurance** — GRA triad + symbolic re-execution + Ragas faithfulness = 3 independent verification layers vs 2 in Alpha/Beta.
5. **Cleanest separation of complexity** — simple content stays simple (Route B); complex content gets full graph treatment (Route A).
6. **Smooth production migration path** — swap `GraphStore` protocol from `kuzu_store` to `neo4j_store` when ready, without changing any extraction, synthesis, or validation code.

---

ARCHITECTURE: APPROVED
