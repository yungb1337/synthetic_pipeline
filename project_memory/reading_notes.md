---
name: project-reading-notes
description: What was read, and the single most important takeaways per source
metadata:
  type: project
---

# Reading Notes — SYN1-4 + papers (status)

**Method note (honesty):** I fully read the extracted text of **SYN1, SYN2, SYN3, SYN4** (ChatGPT conversation exports). A background agent was dispatched for the 4 academic PDFs and **returned with zero output** — I do NOT count it as read. Still pending a real read: `2503.14023v2` (synthetic-data survey), `2504.12322v2`, `3548785.3548793` (SIGIR), and `Syna the future of AI` (Peter Lee). Raw text is under `_research_sources/`.

## SYN1 — the birth of the "trust" thesis
- Pipeline: Documents → OCR/Parsing → Cleaning → Chunking → Entity/Rel → **KG (source of truth)** → Prompt Builder → LLM (constrained generator) → Validation `Valid/Unknown/Contradiction`.
- "The LLM doesn't store facts; the KG feeds the LLM as boundary/context; the LLM never replaces the KG."
- Validation must be: **Unknown ≠ Contradiction**; `Unknown` → retrieval from **Trusted Sources** (not raw search) → evidence? Yes → (human review) → update KG → revalidate; No → research/manual queue, **don't update KG without evidence**.
- Confidence = **multi-dimensional** (graph agreement, medical correctness, policy, completeness, novelty, consistency), a dashboard, not one score.
- Real platform never ends; closed loop: deploy → monitor → failure detection → weakness analysis → generate *only missing* cases → repeat.
- Named two missing pieces: **human review**, **rule/constraint engine** (a graph can be internally consistent yet clinically impossible — "3-month-old with heart attack"), **dataset versioning**.
- Rule engine = business rules/dip child; not graph reasoning.

## SYN2 — 90% engineering not AI; "you're a school project, prove me wrong"
- A real synthetic-data company is 90% data-engineering + validation + infra + customer platform; generation is ~10%.
- Listed 20 missing layers. The ones that matter most: data lake (raw→clean→normalized→KG→embeddings→synthetic, **never overwrite**); **data lineage** (every record reproducible: guideline/paper/policy/prompt/LLM/temp/seed); **version everything** (KG vN, prompt vN, dataset vN — WHO updates → don't rewrite 40M patients); **tenant isolation** (customer A never sees customer B); **statistical validation** (population/distribution/correlation similarity), **privacy validation** (re-id / membership-inference / DP), evaluation platform, orchestration (parser/entity/graph/generator/validation worker pools), explainability, feedback loop, APIs, dashboard, security, observability.
- **Biggest conceptual chord:** KG is not a static reference; it's the "operating system" everything rotates around (medical sources → KG platform → prompt/validation/analytics/quality/search → customer APIs → feedback → incremental KG updates).
- Scale across domains: **separate platform capabilities from domain knowledge**. Reusable platform = ingestion/parsing, KG construction, ontology mgmt, generation, validation, versioning+lineage, privacy+quality evaluation, APIs/dashboards. Domain plugins per vertical.

## SYN3 — "you are at ocean 35-40%", ontology | KG isn't magic
- Key gaps: **ontology layer FIRST** (Heart Attack / Myocardial Infarction / MI must map to one canonical node — ICD-10/SNOMED/LOINC/FHIR), graph evolution engine (merge/split/diff), **domain plugin system**, workflow engine (queues/retries/DLQ/priority), human-in-the-loop portal, **rule engine as safety net**, simulation (longitudinal disease progression), scenario generator, agent architecture (one responsibility per agent), **hybrid retrieval layer** (graph+vector+ontology+rules), benchmarking, cost optimizer (small model→big model→human), memory/cache, dataset compiler (FHIR/CSV/Parquet/SQL/Delta/Snowflake/BigQuery/Mongo/HF), constrained optimizer, continuous learning, multimodal, trust layer.
- **Strategic rec:** don't design the platform around synthetic data generation — **design an Enterprise Knowledge Platform**, build synthetic generation as the *first product* on it. (This matches the user's own reframe.)
- Indirectly, a strongly balanced view: KG is genuinely good for explainability/rule-ignition/multi-hop/changing knowledge, but **not** a replacement for SQL, vector DB, or rules; use vector/search where search is the ask.
- Hospital reality: dozens of heterogeneous inputs (EHR tables, unstructured doctor notes, lab data, radiology reports, DICOM, guidelines, policies, FHIR JSON, billing, ICU time-series). **First job = unify heterogeneous data into one representable form.**

## SYN4 — the parser architecture "aha"
- Logical modules ≠ runtime services. Modular monolith beats microservices. Potential power of "car factory" — one pass, no re-reads, batch embeddings.
- **Full product lifecycle** (10 platforms), but the parser-relevant part: **Ingestion → Parsing → OCR → Layout → Tables → Images → Metadata → Normalization → Chunking → Embeddings → Versioning → Workflow → Monitoring; then Knowledge; then Generation; then Trust; then Delivery; then Continuous learning.**
- **The architectural change that "pays off later": introduce a canonical Document Object Model (DOM)** immediately after parsing and before normalization. Single DOM for all format parsers ⇒ parser independence; each new format (DICOM, CAD) just a new extractor that builds the same Document. No downstream changes.
- **Extraction Pipeline redesign:** oldest `Parser` — combo: Layout is a single-track extractor, not a separate platform. Read once → parallel Text/Layout/Table/Image/Metadata/OCR/Annotation extractors → build_document() → canonical DOM.

## Cross-source contradiction I must hold
KG-as-source-of-truth (SYN1/2) vs "defer/avoid KG for MVP, hybrid retrieval" (SYN3). Recorded as an open decision for the KG phase; does not block the parser.

**Not yet read:** the 4 academic PDFs — interrupted subagent returned empty. Defer proper digestion to next session (do not fabricate).