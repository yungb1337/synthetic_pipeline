# Production Document Parser: Operational Nuance & Deep-Dive Analysis

**Document ID:** `DOC-OP-DEEPDIVE-2026-09-18`  
**Vertical:** Healthcare / Enterprise Knowledge Platform (**MedFactory AI**)  
**Companion File:** [`docs/production-parser-architecture-assessment.md`](production-parser-architecture-assessment.md)  
**Scope:** In-depth operational mechanics, empirical telemetry, concrete code paths, and production risk analysis across Routing, Execution, Storage, Chunking, and Evaluation.

---

## Operational Nuance & Analysis Table

| # | Question / Operational Area | Current Answer & Empirical Reality (with Code / Telemetry References) | Risk if Unaddressed |
| :--- | :--- | :--- | :--- |
| **1** | **Routing Internals & Escalation Ceilings** | • **Complexity Weights:** [`app/routing/config.py:15-31`](app/routing/config.py#L15-L31) sets absolute signal weights: `table_prob=25.0`, `reading_order_ambiguity=20.0`, `ocr_required=18.0`, `scanned_prob=15.0`, `multi_column=15.0`, `font_diversity=15.0`, `layout_complexity=15.0`, `low_text_ratio=12.0`, `low_char_density=8.0`, `form_prob=5.0`, `image_density=5.0`, `block_fragmentation=2.0`.<br>• **Complexity Bands:** [`config.py:65-69`](app/routing/config.py#L65-L69) establishes `0–30: native`, `31–60: enrichment`, `61–100: docling`.<br>• **Low-Confidence Escalation:** [`config.py:73-75`](app/routing/config.py#L73-L75) & [`app/routing/policy.py:26-35`](app/routing/policy.py#L26-L35) escalate by one tier if confidence $< 0.50$ (native $\rightarrow$ enrichment) or $< 0.35$ (enrichment $\rightarrow$ docling).<br>• **Escalation Ceiling:** `docling_low_conf = 0.0` (ceiling: top tier bounded, never escalates further). If Docling fails during execution, [`app/parser/assembler.py:172-194`](app/parser/assembler.py#L172-L194) retries the page up to 2 times. If retries fail, it marks the page `DEAD` and dead-letters the document with zero silent loss. | **Medium-High:** Without a secondary fallback engine at the top tier (e.g. falling back to OCR + heuristic layout if Docling crashes on corrupt fonts), a fatal Docling failure forces document dead-lettering. |
| **2** | **Corpus Telemetry & Docling Bypass** | • **Measured Corpus:** 945 PMC Open-Access Research PDFs (13,132 pages) in [`docs/parser-full-corpus-report-2026-09-14.md`](docs/parser-full-corpus-report-2026-09-14.md).<br>• **Real Route Breakdown:** **97.5% bypass rate** (632 docs / 66.9% enrichment, 289 docs / 30.6% native, 24 docs / 2.5% docling). Matches the assessment report's $>95\%$ estimate.<br>• **Per-Page Latency:** Native $\sim 15\text{--}30\text{ ms}$, Enrichment $\sim 20\text{--}50\text{ ms}$ (unrendered) / $\sim 800\text{--}1200\text{ ms}$ (OCR rendered), Docling $\sim 1500\text{--}2500\text{ ms}$.<br>• **Aggregated Latencies:** Median (P50) page latency $= \mathbf{709.3\text{ ms}}$, Mean page latency $= \mathbf{889.8\text{ ms}}$, P95 page latency $= \mathbf{2,021.0\text{ ms}}$. Median single-doc latency $= \mathbf{7.96\text{ s}}$ (Mean $= 11.51\text{ s}$). Effective throughput $= \mathbf{4.693\text{ pages/s}}$ across 4 shards on 16 CPU cores. | **Low:** Telemetry confirms that native/enrichment routing prevents Docling resource saturation and provides stable multi-core throughput. |
| **3** | **LLM Judge Invocation & Feedback Loop** | • **Execution Path:** [`scripts/llm_judge.py`](scripts/llm_judge.py) is strictly an **offline CLI evaluation harness** (`scripts/run_stratified_judge.py`, `scripts/run_1000_evaluation_pipeline.py`). It is **never invoked during synchronous document extraction** ([`app/parser/extraction.py`](app/parser/extraction.py)).<br>• **Latency & Cost:** Latency is $\sim 1.5\text{--}3.5\text{ s/doc}$. Token cost on `gemini-3.5-flash-lite` ($\sim 2,000$ in / $300$ out) is $\sim \$0.0001\text{--}\$0.0003\text{/doc}$.<br>• **Feedback Loop:** Judge outputs are stored in `judgments/` or `checkpoints/` and are **computed and unused** by runtime modules. The verdict does not update `Provenance`, router weights, or document acceptance. | **Medium:** High-value post-parse evaluation data is generated during test runs but never harnessed at runtime to trigger automated human review or re-routing. |
| **4** | **Dead-Letter Rate & Assembler Retries** | • **Actual Dead-Letter Rate:** **0.0%** across both the 945-doc PMC corpus (13,132 pages) and 1,000-doc medical run (`docs/parser-full-corpus-report-2026-09-14.md`). 0 failed docs, 0 dead letters, 100% assembly pass rate (`actual_pages == expected_pages`).<br>• **Historical Cause:** Prior to ADR-013 / Group B fixes, failures were caused by concurrent Docling/OCR C++ heap fragmentation (`std::bad_alloc`).<br>• **Retry Parameters:** [`app/parser/config.py:87`](app/parser/config.py#L87) sets `page_retries = 2`. [`app/parser/assembler.py:174`](app/parser/assembler.py#L174) implements linear backoff: `time.sleep(min(0.5 * attempt, 2.0))` (Attempt 1 = 0.5s, Attempt 2 = 1.0s). If still missing, pages are marked `DEAD` and the document is dead-lettered without emitting a partial DOM. | **Low:** Current isolation and retry mechanics successfully prevent silent data loss and eliminate dead letters on standard research and medical corpora. |
| **5** | **Table Serialization in Semantic Chunking** | • **Actual Code Path:** [`app/chunking/chunker.py:185-230`](app/chunking/chunker.py#L185-L230) (`_resolve_order`) iterates **exclusively over `doc.reading_order`**, which contains `Block.id`s only ([`builder.py:82`](app/parser/dom/builder.py#L82)).<br>• **Table Exclusion:** In [`app/parser/engines/native_pdf.py:167`](app/parser/engines/native_pdf.py#L167), blocks overlapping tables by $>60\%$ are stripped to prevent duplicates. Consequently, **DOM `Table` objects are completely omitted from semantic chunks and vector embeddings**.<br>• **Schema Status:** `source_table_ids` and `kind="table_atomic"` in [`app/chunking/schema.py:37, 53`](app/chunking/schema.py#L37-L53) are placeholders marked *"reserved for atomic table/figure-caption chunks (not built this run)"*. | **CRITICAL:** While tables exist cleanly in the DOM (`dom-v0.1.0.docJSON`), downstream RAG and embedding retrieval **cannot search or retrieve tabular clinical data** because tables are missing from `chunks-v1.json`. |
| **6** | **Confidence Calibration & 0.85 Threshold** | • **Calibration Reality:** The proposed `0.85` auto-accept threshold is an **uncalibrated heuristic recommendation** from the initial architecture review, **not an empirical threshold**.<br>• **Existing Telemetry:** Routing thresholds in `app/routing/config.py` were tuned to balance throughput distributions, and LLM Judge evaluations verified general layout scores, but **zero double-blind human clinician error rate vs. confidence bucket calibrations exist** in the repository. | **HIGH (Regulatory):** In a regulated pipeline (e.g. FDA / HIPAA / 21 CFR Part 11), deploying an arbitrary uncalibrated acceptance threshold risks silently approving hallucinated or dropped clinical data into synthetic sets. |
| **7** | **Versioning, Reprocessing & DOM Diffs** | • **Storage Layout:** [`app/parser/storage.py:32-46`](app/parser/storage.py#L32-L46) stores DOMs under `dom/<doc_id>/dom-v{version}.docJSON` and `norm-v{version}.docJSON`. Raw binaries are immutable in `raw/<sha256>.<ext>`.<br>• **Reprocessing Path:** Incrementing `parser_version` (e.g. to `v0.2.0`) writes a new versioned file alongside prior outputs (append, never destroy).<br>• **Divergence / Gap:** There is **no automated backfill orchestrator, migration pipeline, or semantic DOM diff tool** to compare `dom-v0.1.0` vs `dom-v0.2.0` across millions of documents or explain bounding box / text regressions between model updates. | **MEDIUM (Compliance):** Regulators require auditability when re-running historical data under updated models; without a semantic diff tool, explaining why an extraction changed is a manual task. |
| **8** | **Deduplication & Re-Parse Checking** | • **Interactive Mode (`resume=False`, default):** [`app/parser/extraction.py:116-156`](app/parser/extraction.py#L116-L156) calculates `doc_id = f"d-{sha[:16]}"` but **does not check if the DOM already exists**; it parses the document again and atomically overwrites `dom/<doc_id>/dom-v<version>.docJSON`.<br>• **Batch Mode (`resume=True`):** [`extraction.py:129-155`](app/parser/extraction.py#L129-L155) checks `self.ledger.load_plan(doc_id)`. If `assembly.status == "ok"` and the DOM exists, it returns immediately in $<1\text{ ms}$.<br>• **Raw Deduplication:** `Store.put_raw` ([`storage.py:54-59`](app/parser/storage.py#L54-L59)) is write-if-absent, ensuring duplicate raw files are stored only once on disk. | **Low-Medium:** Ingesting duplicate files via interactive API calls re-burns compute unless callers explicitly pass `resume=True`. |
| **9** | **Deployment Architecture & Burst Load** | • **Scaling Model:** Single-host multi-threaded and multi-process (`--shards N` SHA256 modulo partitioning over file lists, as in [`scripts/run_all_waves.py`](scripts/run_all_waves.py)). Basic HTTP server in [`app/parser/docling_service.py`](app/parser/docling_service.py) provides single-page remote Docling offloading.<br>• **Burst Behavior:** Incoming batch tasks buffer in Python `ThreadPoolExecutor` queues in RAM. When concurrency limits are reached, tasks block in memory. Host memory is protected by the `ResourceGovernor` ([`scheduler.py`](app/parser/scheduler.py)), but there is **no persistent broker queue, no rate-limiting, and no HTTP 429/503 backpressure**. | **HIGH (Scale & Ops):** Under high-volume ingestion or sustained API traffic bursts, lack of an external message queue (e.g. RabbitMQ / Redis / SQS) will cause process memory bloat and dropped HTTP connections. |

---

## Technical Deep-Dives

### 1. Routing Internals, Weight Formulation & Escalation Ceiling

The intelligent router ([`app/routing/router.py`](app/routing/router.py)) evaluates a PDF stream through [`FastInspector`](app/routing/inspectors.py), passing observed features to 9 detectors. 

#### Complexity Scoring Formulation
In [`app/routing/scoring.py:79-89`](app/routing/scoring.py#L79-L89), complexity is computed as an **absolute weighted sum** of positive signals:

$$\text{Complexity} = \min\left(100.0, \max\left(0.0, \sum_{i \in \text{positive}} w_i \cdot v_i\right)\right)$$

Where positive weights ($w_i > 0$) from [`app/routing/config.py`](app/routing/config.py) are:
- `metric_table_probability`: $25.0$
- `metric_reading_order_ambiguity`: $20.0$
- `metric_ocr_required`: $18.0$
- `metric_scanned_page_probability`: $15.0$
- `metric_font_diversity`: $15.0$
- `metric_multi_column_probability`: $15.0$
- `metric_layout_complexity`: $15.0$
- `metric_low_text_ratio`: $12.0$
- `metric_low_char_density`: $8.0$
- `metric_form_probability`: $5.0$
- `metric_image_density`: $5.0$
- `metric_block_fragmentation`: $2.0$
- **Total Positive Weight Mass ($\Sigma w_i$):** $150.0$

Confidence is defined as the fraction of positive weight mass that was successfully measured:

$$\text{Confidence} = \frac{\sum_{i \in \text{measured}} w_i}{\sum_{i \in \text{all positive}} w_i}$$

#### Band Mapping & Escalation Logic
In [`app/routing/policy.py:18-35`](app/routing/policy.py#L18-L35):
1. **Raw Tier Assignment:**
   - $[0, 30] \rightarrow \text{"native"}$
   - $[31, 60] \rightarrow \text{"enrichment"}$
   - $[61, 100] \rightarrow \text{"docling"}$
2. **Confidence Escalation Gate:**
   - If $\text{Tier} == \text{"native"}$ and $\text{Confidence} < 0.50 \rightarrow \text{Escalate to "enrichment"}$.
   - If $\text{Tier} == \text{"enrichment"}$ and $\text{Confidence} < 0.35 \rightarrow \text{Escalate to "docling"}$.
   - If $\text{Tier} == \text{"docling"}$, $\text{threshold} = 0.0$ $\rightarrow$ **No further escalation (Ceiling reached)**.

---

### 2. Empirical Corpus Telemetry & Latency Breakdown

From the benchmark across **945 PMC Research Documents (13,132 Pages)** on 16 CPU cores ([`docs/parser-full-corpus-report-2026-09-14.md`](docs/parser-full-corpus-report-2026-09-14.md)):

```
Full 945-Doc Route Distribution:
================================
├── [Enrichment Fast Path] : 632 docs (66.9%)  ── Avg: ~20-50 ms/page
├── [Native Fast Path]     : 289 docs (30.6%)  ── Avg: ~15-30 ms/page
└── [Docling Heavy Path]   :  24 docs ( 2.5%)  ── Avg: ~1,500-2,500 ms/page
```

#### Latency Quantiles (Corpus-Wide):
- **Page Latency P50 (Median):** $709.3\text{ ms}$
- **Page Latency Mean:** $889.8\text{ ms}$
- **Page Latency P95:** $2,021.0\text{ ms}$
- **Document Latency P50 (Median):** $7.96\text{ s}$
- **Document Latency Mean:** $11.51\text{ s}$
- **Effective Multi-Process Throughput:** $4.693\text{ pages/s}$ ($0.338\text{ docs/s}$) across 4 parallel shards.
- **Docling Bypass Efficiency:** **$97.5\%$** of documents (921 / 945) completely bypass Docling neural models, reducing host RAM by $\sim 85\%$.

---

### 3. Code Path Analysis: DOM Table Chunking Omission

Inspection of [`app/chunking/chunker.py`](app/chunking/chunker.py) reveals how tabular data is handled during chunking:

```python
# app/chunking/chunker.py:47-49
items, order_info = self._resolve_order(doc)

# app/chunking/chunker.py:204-216
chain = doc.reading_order or []
items: list = []
if chain:
    for bid in chain:
        b = id_to_block.get(bid)
        if b is not None:
            items.append((b, "reading_order"))
```

* **Mechanism:** `_resolve_order` iterates **only** over `doc.reading_order`, which consists strictly of text `Block` identifiers (`f"{document_id}/b{page}_{seq}"`).
* **Table Filtering:** In [`app/parser/engines/native_pdf.py:160-170`](app/parser/engines/native_pdf.py#L160-L170), text blocks overlapping extracted tables by $>60\%$ are removed:
  ```python
  table_bboxes = [t.bbox for t in valid_tables if t.bbox]
  extracted_blocks = [
      b
      for b in all_blocks
      if not any(_bbox_overlap_ratio(b.bbox, tb) > 0.6 for tb in table_bboxes)
  ]
  ```
* **Impact:** Table rows and cells in `doc.pages[i].tables` are **never flattened, serialized to markdown, or converted into chunks**.
* **Finding:** While tables are preserved in the DOM (`dom-v0.1.0.docJSON`), the retrieval layer (`chunks-v1.json` and `vectors.npy`) contains **zero table content**.

---

### 4. Zero-Loss Verification & Dead-Letter Mechanics

#### The Dead-Letter Gate ([`app/parser/assembler.py`](app/parser/assembler.py))
1. The `SourceScan` establishes `expected_page_set = [0, 1, ..., N-1]` from `fitz.open().page_count`.
2. Pages are processed and returned as `PageResult`s.
3. `DocumentValidator.classify()` checks:
   $$\text{assembled\_page\_set} \equiv \{p \mid \text{PageResult}(p).\text{status} \in (\text{OK}, \text{PARTIAL})\} \cup \{\text{valid blank pages}\}$$
4. If $\text{assembled\_page\_set} \neq \text{expected\_page\_set}$, the Assembler triggers retry loops:
   - Config: `page_retries = 2` ([`app/parser/config.py:87`](app/parser/config.py#L87)).
   - Backoff: `time.sleep(min(0.5 * attempt, 2.0))` ([`assembler.py:174`](app/parser/assembler.py#L174)).
5. If pages remain unparsed after 2 retries, they are marked `DEAD`.
6. **Hard Gate Guarantee:**
   - If any page is `DEAD`, `report.document` is set to `None`, `dom_key` is set to `None`, and the document is written to `manifest/<doc_id>/plan.json` with status `"dead"` or `"partial"`.
   - **No corrupt or incomplete DOM is ever written to storage.**

---

### 5. Architectural Recommendations for Next Milestone

Based on this operational deep-dive, the three most critical implementation tasks are:

1. **Table Chunking Seam (Module #3 Extension - CRITICAL):**
   Update [`app/chunking/chunker.py`](app/chunking/chunker.py) to walk `doc.reading_order_full` (which interleaves `block`, `table`, and `image` entries) instead of `doc.reading_order`. Serialize `Table` objects into structured Markdown/HTML or atomic table chunks (`kind="table_atomic"`) so tabular data is searchable in the vector index.
2. **Empirical Confidence Calibration (Regulatory - HIGH):**
   Construct a 100-document double-blind ground-truth validation set (with clinician annotations) to calibrate confidence scores across OCR, layout, and table extraction. Replace heuristic acceptance thresholds with empirical ROC / Expected Calibration Error cutoffs.
3. **Queue-Based Ingest & Burst Ingestion (Infrastructure - HIGH):**
   Wrap `Extractor.extract` behind an asynchronous worker queue (e.g. Redis Queue / Celery) with dead-letter topics and HTTP 429/503 backpressure to decouple incoming ingestion bursts from local process pools.

---
*Operational Deep-Dive completed on 2026-09-18.*
