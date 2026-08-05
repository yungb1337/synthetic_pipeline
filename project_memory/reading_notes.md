---
name: project-reading-notes
description: What was read, and the single most important takeaways per source
metadata:
  type: project
---

# Reading Notes — SYN1-4 + papers (status)

**Method note (honesty):** I fully read the extracted text of **SYN1, SYN2, SYN3, SYN4** (ChatGPT conversation exports). The 4 academic PDFs were read in full on **2026-08-04** (their takeaways are appended below, and a full synthesis is in the audit run's context + `checkpoints/`). Raw text is under `_research_sources/`.

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

## Papers — read 2026-08-04 (full text, in `_research_sources/`)

### `2503.14023v2` — "Synthetic Data Generation Using LLMs: Advances in Text and Code" (survey)
- **Fact:** LLM synthetic augmentation lifts low-resource text tasks **3–26%** (100 real + 100 synthetic; gains shrink as real data grows).
- **Fact:** GPT-3 labeled 3000 SST-2 samples for **$14.37 in 46 min** vs **$221–300 + 1000 min** human labeling — but 6000 synthetic samples hit **76%** accuracy vs **88%** for 3000 human-labeled: a real quality gap.
- **Fact:** retrieval-augmented generation **grounds outputs and reduces hallucination**; mixing synthetic+real **avoids model collapse** (Gerstgrasser 2024); synthetic is *not* safe by default (a fabricated patient record could match a real person).
- **Failure points:** hallucination/factual error; **distribution shift** (synthetic "too clean"); bias amplification; **model collapse in closed loops**; verbatim regeneration / privacy leakage; diminishing returns on volume; evaluation is fuzzy for text (unlike code's pass/fail oracle).
- **Implementation:** retrieve-to-ground, filter (dedup, prompt-leakage, critic classifiers, **consistency checks** — LLM contradicts on rephrase → unreliable), keep a real-data core, constrain generation + tools-in-loop, human-review a subset, statistical rigor (CIs, ablations).

### `2504.12322v2` — "GRA: A Strategic Coordination Framework of Small LLMs Matches Large LLMs in Data Synthesis"
- **Fact:** Generator–Reviewer–Adjudicator (multi small LLM) **matches/exceeds 72B distillation** (8.83% better avg on Qwen-7B base).
- **Fact:** **single-model review ≈ no review** (negligible gains); multi-model committee review is what works. Reviewer = mean score + std on 6 dims (Correctness, Clarity, Completeness, Relevance, Coherence, Ethicality) → accept / reject / **adjudicate on disagreement**.
- **Fact (case study):** 2 of 3 reviewers wrongly scored a bad sample high; **mean passed but high variance (2.48) triggered adjudication**, which correctly discarded it — majority vote alone fails.
- **Failure points:** majority voting fails when the majority is wrong; fixed role assignment < randomized; per-iteration returns plateau.
- **Implementation:** committee + mean/std thresholds → 3-way gate; embedding-based semantic dedup (cosine ≤0.9); keyword+summary metadata. **This is our `Valid/Unknown/Contradiction` + multi-reviewer + adjudicator pattern, proven.**

### `3548785.3548793` — "Synthetic Data Generation: A Comparative Study" (IDEAS'22, SIGIR)
- **Fact:** GAN/VAE/SDV-GAN **fail on large datasets** (memory errors); GAN = mode collapse/non-convergence/hyperparameter-sensitive; VAE ~4 days on 30k records; copula only handles **linear** correlations (no tail dependence). SMOTE/SP-NP best fidelity, DS fastest.
- **Failure point:** the **evaluation tooling itself doesn't scale** (SD Metrics memory-errors on large data); text generation is far harder to evaluate than tabular (no definitive oracle).
- **Implementation:** proximity/pairwise-correlation + SD Metrics (statistical/detection/efficacy/**privacy**) are the statistical-validation vocabulary — detection = discriminator, privacy = re-identification risk. Generator is commodity; **the validation/provenance layer is the differentiator.**

### `Syn and the future of ai peter Lee-final-2` — Peter Lee, "Synthetic Data and the Future of AI" (Cornell L. Rev.)
- **Fact:** data difficulties challenge **96% of companies**; sourcing/cleaning/labeling eats **80% of data-scientist time**; Gartner projected 60% of AI training data synthetic by 2024.
- **Fact:** **IBM Watson Health gave incorrect cancer-treatment advice trained on erroneous synthetic patient records** — the canonical "low-quality synthetic ≈ patient harm" failure.
- **Fact:** **model collapse** (Shumailov, Nature 2024): recursive synthetic training pollutes next generations; best antidote = real data + **high-quality synthetic + coordinated provenance sharing**.
- **Fact:** "Synthetic data derived from methods without complete documentation **cannot be validated**" (Synthea/Walonoski) — provenance is a hard requirement, not a nicety.
- **Fact:** synthetic *mitigates* but never eliminates privacy risk — re-identification, leakage/reconstruction, and the FTC's **"algorithmic destruction"** precedent (Weight Watchers, Everalbum: forced to delete data AND the models trained on it).
- **Implementation:** "very specific, sophisticated frameworks and metrics that validate it created what it set out to create" are essential; disclosure/transparency; human-in-the-loop for fairness. Open-source reference approaches: **Synthea** (synthetic patients), **SDV**, Gretel.ai, **SmartNoise** (differential privacy).

## Cross-source synthesis (2026-08-04)
The four papers independently converge on one conclusion that validates the project's thesis: **the generator is commodity; retrieval-grounding, multi-stage validation, privacy checks, and provenance are the actual product.** GRA's accept/reject/adjudicate is the empirical shape of our `Valid/Unknown/Contradiction` design; the survey's retrieve-to-ground is our "Unknown → trusted-source retrieval → evidence → KG update" loop; Peter Lee's Watson + model-collapse + "cannot be validated without documentation" are the failure modes our lineage/versioning/validation layers exist to prevent.