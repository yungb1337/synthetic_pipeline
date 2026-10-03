# Knowledge Graph Construction & Architecture Blueprint (`app/kg/`)

This document provides the exhaustive architectural and data-engineering specification for Module #1: Knowledge Graph Construction (`app/kg/`), connecting our Canonical DOM parser output to a verifiable, provenance-anchored Knowledge Graph.

---

## 1. Source Data Mapping (Canonical DOM $\rightarrow$ Knowledge Graph)

### A. Data Origins & Required DOM Fields
The Knowledge Graph does not ingest raw binary files (PDF, DOCX, scans). It strictly consumes the normalized, canonical Document Object Model emitted by `app/parser` and cleaned by `app/normalizer` (`app/parser/dom/models.py`).

| Canonical DOM Element | DOM Field & Type | Survival Requirement | Target KG Field / Graph Concept | Extraction Role & Significance |
| :--- | :--- | :--- | :--- | :--- |
| **Document** | `document_id: str` | **Mandatory** | `Graph.doc_id` / `Provenance.doc_id` | Global root identifier for corpus multi-tenancy and lineage. |
| **Document** | `source_hash: str` | **Mandatory** | `Provenance.source_hash` | Cryptographic SHA-256 anchoring graph facts to immutable raw bytes. |
| **Metadata** | `title, author, created: str` | **Mandatory** | `DocumentNode.metadata` | High-level provenance node attributes; prevents cross-document attribution drift. |
| **Block (Paragraph)** | `id: str` | **Mandatory** | `Edge.provenance.block_ids` | Content-addressed anchor proving which text span generated an entity/relation. |
| **Block (Paragraph)** | `text: str` | **Mandatory** | `Edge.evidence_text` | Exact verbatim context string used for LLM extraction and validation. |
| **Block (Paragraph)** | `bbox: BBox (x0,y0,x1,y1)` | **Mandatory** | `Provenance.spatial_coords` | Visual bounding box on physical page (points) for interactive UI audit. |
| **Block (Paragraph)** | `page: int` | **Mandatory** | `Provenance.page_index` | Page number in source document. |
| **Block (Paragraph)** | `confidence: float` | **Mandatory** | `Provenance.ocr_confidence` | Propagated error signal (1.0 for native text, $<1.0$ for OCR text). |
| **Block (Heading)** | `kind: "heading"` + `text` | **Mandatory** | `SectionContextNode` / Edge Scope | Provides hierarchical scope (e.g. "Contraindications", "Dosage & Administration"). |
| **Table** | `id: str`, `page: int` | **Mandatory** | `TableNode` / `Edge.provenance.table_id` | Unique identifier of tabular structure. |
| **Table** | `caption: str` | **Mandatory** | `TableNode.caption` | Semantic description of table entity relationships. |
| **Table** | `header: list[str]` | **Mandatory** | `RelationPredicate` / Column Typings | Direct source of relation types or attribute names in structured grids. |
| **Table** | `rows: list[Row]` $\rightarrow$ `Cell.text` | **Mandatory** | `EntityNode` & `RelationEdge` | Entity attribute pairs (e.g. Drug row $\times$ Adverse Event column $\rightarrow$ Rate cell). |
| **Table** | `Cell.bbox` / `Row.bbox` | **Mandatory** | `Provenance.cell_coord` | Exact cell coordinate $(row, col)$ and spatial box for micro-auditing. |
| **Reading Order** | `reading_order_full: list[Entry]` | **Mandatory** | `Graph.traversal_sequence` | Sequential order resolving intra-document coreferences and multi-paragraph statements. |
| **References** | `references: list[Reference]` | **Useful** | `CitationEdge` | Resolves inline citations $[n]$ to external bibliographic entities. |
| **Regions** | `regions: list[Region]` | **Useful** | `StructuralContext` | Separates sidebars, footnotes, and headers from main body narrative. |

### B. What Information is Lost if Dropped?
1. **Dropping `bbox` / `block_id`:** Destroys fine-grained auditability. If an LLM generates a hallucinated medical fact, the system cannot verify if the source text actually asserted it.
2. **Dropping `kind: "heading"`:** Flattens document semantics. For example, a drug named in the "Contraindications" section would be extracted without negative relational framing, leading to catastrophic medical errors.
3. **Dropping `Table.header` / `caption`:** Degrades table cells into disconnected token blobs, making multi-column relational extraction virtually impossible.

---

## 2. Ontology Specification & Architecture

### A. Ontology Sources & Construction Strategy
An ontology defines the permissible semantic vocabulary: entity classes ($C$), relation types ($R$), attribute schemas ($A$), and ontological axioms ($\Sigma$).

```
                ┌──────────────────────────────────────────────┐
                │          Domain Ontology (TBox)              │
                │  - Classes: Disease, Drug, Procedure, Gene   │
                │  - Relations: TREATS, CONTRAINDICATES, CAUSES│
                │  - Axioms: Domain(TREATS) = Drug/Procedure   │
                │            Range(TREATS) = Disease           │
                └──────────────────────────────────────────────┘
                                       │
                    Constrains & Validates Extraction
                                       │
                                       ▼
┌──────────────────────┐       ┌──────────────────────────────┐       ┌──────────────────────┐
│  Canonical DOM Block │ ────> │  Structured Extractor (LLM)  │ ────> │ KG Instance (ABox)   │
│  (Paragraph / Table) │       │  (Pydantic / Grammar Masked) │       │ (Triples + Lineage)  │
└──────────────────────┘       └──────────────────────────────┘       └──────────────────────┘
```

#### Strategy Recommendation: **Hybrid Layered Ontology**
1. **Core Base TBox (Pre-defined Pydantic Schemas):**
   * Pre-defined schema using established medical/enterprise ontologies (e.g. **UMLS Semantic Network**, **SNOMED-CT**, **ICD-10-CM**, **RxNorm**, or **Schema.org** for general business).
   * Enforced strictly at runtime via Pydantic models with `Literal` relation sets and validation hooks.
2. **Emergent Domain Extension (LLM Induction with Human-in-the-Loop):**
   * When the extractor encounters high-confidence entities/relations outside the pre-defined TBox, it flags them as candidate extensions (`_emergent_relation: str`) rather than silently dropping them.
   * Periodically reviewed and promoted into the formal schema.

### B. Ontology Modeling Mechanics
* **Hierarchical Types (Taxonomy):** Modeled via `is_a` / `subClassOf` inheritance in Pydantic. E.g., `Antibiotic` $\subset$ `PharmaceuticalProduct` $\subset$ `MedicalEntity`.
* **Aliases, Synonyms & CUIs:** Every entity node maintains a canonical name, a set of text aliases (surface forms), and standard ontology concept unique identifiers (e.g. `cui: "C0011849"`, `snomed_code: "386661006"`).
* **Cardinality & Logical Axioms:**
  * Domain & Range checking: $R(E_1, E_2) \implies E_1 \in \text{Domain}(R) \land E_2 \in \text{Range}(R)$.
  * Multiplicity rules: `has_primary_diagnosis` has max cardinality 1; `has_symptom` has max cardinality $N$.

---

## 3. Entity Extraction Architecture

### A. Comparison of Entity Extraction Approaches

| Approach | Required Data | Accuracy (F1) | Cost / 1k Pages | Latency / Page | Determinism | Domain Adaptation | Failure Modes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Rules / Regex** | Curated patterns (DOBs, dosages, codes) | High on formats ($>95\%$), Zero on NL ($<10\%$) | $\approx \$0.00$ | $<1$ ms | $100\%$ | Poor (requires manual re-writing) | Misses paraphrases, rigid syntax matching. |
| **2. Dictionaries / FlashText** | Gazetteers (UMLS Metathesaurus, RxNorm) | High Precision ($90\%$), Low Recall ($45-60\%$) | $\approx \$0.00$ | $2-5$ ms | $100\%$ | Moderate (needs vocabulary list updates) | Polysemy, out-of-vocabulary terms, abbreviations. |
| **3. Classical NLP (SpaCy / Stanza)** | Tokenized text + Pre-trained models | Moderate ($70-80\%$) | $\approx \$0.00$ | $10-20$ ms | High | Poor for specialized out-of-domain terms | Over-generalization, fails on domain jargon. |
| **4. Zero-Shot Transformer (GLiNER)** | Label list + Raw text chunks | High ($84-91\%$) | Low (local GPU/CPU, $\approx \$0.05$) | $30-60$ ms | High | Excellent (zero-shot arbitrary entity types) | Nested entity overlap, boundary clipping. |
| **5. Standard LLM Prompting** | Text + Free-form prompt | High ($85-92\%$) | High ($\$2.00-\$10.00$) | $500-2000$ ms | Low | Very High | JSON syntax errors, hallucinations, naming drift. |
| **6. Structured Output LLM (Instructor / Outlines)** | Text + Pydantic Schema | Very High ($92-96\%$) | High ($\$2.00-\$10.00$) | $600-2200$ ms | High | Very High | High cost, rate limits, latency bottlenecks. |
| **7. Hybrid Pipeline (GLiNER + LLM Arbiter)** | Text + Vocabulary + Schema | SOTA ($95-98\%$) | Moderate ($\$0.30-\$1.00$) | $80-250$ ms | High | SOTA | Pipeline complexity. |

### B. Chosen Architecture: **Two-Stage Hybrid Entity Pipeline**
1. **Stage 1 (High-Speed Candidate Proposal):** GLiNER / dictionary gazetteer extracts candidate entity spans and types from DOM blocks at $50$ms/page.
2. **Stage 2 (Contextual Disambiguation & Normalization):** High-ambiguity spans and multi-token clinical concepts are resolved via a structured-output LLM call using Pydantic schemas.
3. **Special Handling Logic:**
   * **Nested Entities:** (e.g. `[Type 2 Diabetes Mellitus [with Nephropathy]]`) $\implies$ Represented as composite entity with sub-concept pointer.
   * **Coreference Resolution:** Fast rule-based + LLM coreference resolution over `reading_order_full` to resolve pronouns ("it", "the medication", "this condition") back to canonical anchor entities.
   * **Cross-Table / Cross-Paragraph Normalization:** Entities are linked across blocks using cosine similarity over BGE-M3 embeddings + string distance (Levenshtein) + CUI linking.

---

## 4. Relation Extraction Architecture

### A. Extraction Scope & Mechanics
Relation extraction maps entity pairs to directed, typed semantic edges:
$$\text{Relation} = \langle E_{\text{source}}, \text{Predicate}, E_{\text{target}}, \text{Metadata}(\text{Confidence}, \text{Evidence}, \text{Lineage}) \rangle$$

```
                                  [ Paragraph / Table Context ]
                                                │
                 ┌──────────────────────────────┴──────────────────────────────┐
                 ▼                                                             ▼
     [ Intra-Sentence Extraction ]                                [ Cross-Structure Extraction ]
  - Direct SVO dependencies                                    - Heading -> Paragraph inheritance
  - GLiNER relation heads                                      - Table Header -> Cell relationships
  - Fast pattern matcher                                       - Cross-paragraph co-reference chain
                 │                                                             │
                 └──────────────────────────────┬──────────────────────────────┘
                                                ▼
                                 [ Candidate Relation Triples ]
                                                │
                                                ▼
                                [ Schema & Directionality Filter ]
                                (Enforce Domain, Range, Predicates)
                                                │
                                                ▼
                                 [ Validated Knowledge Graph Edge ]
```

### B. Cross-Structural Relation Patterns
1. **Intra-Sentence / Intra-Paragraph:** Standard dependency parsing / LLM relation extraction within the block context.
2. **Heading $\rightarrow$ Paragraph Inheritance:** Heading entities establish persistent context. If Heading is `"Adverse Reactions"` and block mentions `"Nausea"`, an edge `(Drug, HAS_ADVERSE_EFFECT, Nausea)` is formed inheriting the subject from the document's active drug entity.
3. **Table Row $\times$ Column Relations:**
   * Header defines Predicate (e.g., `"Dosage"`, `"Route"`, `"Frequency"`).
   * Column 0 entity forms Subject; Column $N$ cell forms Object.
   * Cell coordinates $(r, c)$ and header index are recorded in the edge provenance.
4. **Cross-Page / Cross-Section Relations:** Resolved via global entity ID unification (e.g. Entity $E_{102}$ introduced on Page 2 connected to outcome $E_{840}$ on Page 12).

---

## 5. Provenance & Lineage Model

### A. Complete Lineage Tracing Chain
Every synthetic record must be 100% auditable through the following deterministic chain:

```
[ Synthetic Training Sample ] (e.g. Multi-hop QA, CoT reasoning step)
          │  references
          ▼
[ Subgraph Path / Triple ] (e.g. (Metformin) -[CONTRAINDICATED_IN]-> (Renal Failure))
          │  asserted by
          ▼
[ Graph Edge Provenance Object ]
  ├── doc_id: "doc-med-2026-0042"
  ├── source_hash: "a3f89b...e4"
  ├── page_index: 7
  ├── block_id: "blk-007-014"
  ├── table_id: "tbl-007-002" (if from table)
  ├── cell_coord: [3, 2] (if from table)
  ├── bbox: { x0: 72.0, y0: 340.5, x1: 520.0, y1: 395.2 }
  ├── evidence_text: "Metformin is strictly contraindicated in patients with severe renal impairment (eGFR < 30 mL/min)."
  └── extractor_version: "kg-ext:v1.2.0"
          │  resolves to
          ▼
[ Immutable Canonical DOM File ] (`dom/doc-med-2026-0042/dom-v1.docJSON`)
          │  originates from
          ▼
[ Raw Source File Storage ] (`raw/a3f89b...e4.pdf`)
```

### B. Why Provenance is the Core Trust Product
* **Hallucination Detection:** If an LLM-as-a-Judge or human auditor flags a synthetic statement as doubtful, the system can instantly pull the exact page image with highlighted bounding box coordinates.
* **Continuous Remediation:** If a document is updated or revoked, all synthetic training examples derived from that document's specific `block_id` can be identified and invalidated without regenerating the entire corpus.

---

## 6. Knowledge Graph Validation & Quality Gates

Before the Knowledge Graph can be used for synthetic generation, it must pass automated structural validation gates:

| Validation Gate | Method / Algorithm | Metric / Threshold | Failure Action |
| :--- | :--- | :--- | :--- |
| **1. Ontology Conformance** | Schema type checking (SHACL / Pydantic validator) | $100\%$ valid Domain/Range typing | Drop invalid predicate; flag for review. |
| **2. Provenance Completeness** | Verify every edge has non-empty `doc_id`, `block_id`, `page_index`, and `evidence_text` | $100\%$ complete lineage | Reject edge. |
| **3. Contradiction & Conflict** | Check for opposing binary relations: $(A, \text{TREATS}, B) \land (A, \text{CONTRAINDICATED}, B)$ without qualifier | Detected conflict count $= 0$ unresolved | Mark edge pair as `DISPUTED`; require qualification context. |
| **4. Entity Resolution Density** | Measure ratio of isolated singleton nodes vs connected components | Component density $> 85\%$ connected | Flag unlinked entity clusters for re-linking. |
| **5. Confidence Calibration** | Aggregate OCR confidence $\times$ Extractor confidence | Threshold $\ge 0.80$ | Exclude low-confidence edges from synthetic reasoning paths. |
| **6. Graph Coverage** | Fraction of DOM blocks containing entities represented in the graph | Target $> 75\%$ semantic blocks covered | Report unparsed content in extraction report. |
