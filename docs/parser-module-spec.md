# Module #1 — Parser (Document / Extraction Pipeline)

**Scope owner:** Architect (you + assistant). **Status:** design, awaiting stack confirmation. **Big WHY:** everything downstream (normalize, chunk, embed, KG, generation, validation) must operate on one deterministic, parser-independent object. The parser is the trust boundary for *all* later truth: if extraction is wrong, the KG and every dataset built on it is wrong. It is the highest-leverage module to get right.

---

## 1. Purpose
Convert an arbitrary raw file into a canonical, structured **Document Object Model (DOM)** in a single read, with full provenance, coordinates, layout, tables, images, and reading order — so that no downstream consumer ever touches the original byte stream.

## 2. Responsibilities
- Detect real file type (not just extension).
- Read the file **once** and load a format-neutral in-memory page/model.
- Run parallel extractors: text, layout (reading order), OCR (scanned), tables, images+metadata, document metadata, coordinates, annotations.
- Build the canonical `Document` DOM.
- Serialize + persist (immutable) with `parser_version`, content hash, and provenance.
- Emit a **processing event** for the workflow layer.

## 3. Inputs
- `DocumentUpload` (bytes or object-store ref), `filename`, detected/ext `.`, uploader/tenant context (for lineage), config `ParserConfig`.

## 4. Outputs
- `Document` (DOM, JSON) + `pages/*` + `images/*` + `tables/*` refs.
- `ParseReport` (per-page confidence, extracted blocks, OCR used, per-extractor status).
- Event `document.parsed.v1` with `doc_id`, parser_version, sha256.

## 5. Internal workflow (single pass, one read)
```
signatures sniff
 → container probe (ZIP etc.)
 → content sniff (CSV/JSON/XML/MD) → detected Type + confidence + declared-ext
 → LOAD document into memory once
 → PARALLEL extractors on the shared page object:
     text | layout(reading order) | ocr(if scanned) |
     tables | images(+meta) | metadata | coordinates | annotations
 → build_document()  (a Reading Order Graph over blocks)
 → validate invariants → serialize → persist → publish event
```

## 6. Data flow
`raw -> detector -> loader -> extractors -> DOM -> normalize (next module)`.

## 7. Public APIs
- `POST /v1/documents` (upload, returns `job_id` then callback/event)
- `GET /v1/documents/{id}` (status/DOM)
- `GET /v1/documents/{id}/dom` (canonical object)
- `GET /v1/parse-caps` (supported types, `parser_version`)

## 8. Internal interfaces (Python)
- `Detector`, ditto `Loader` (per type), `Extractor`, `DocumentBuilder`, `Serializer`, `Store` (abstraction), `Workflow port (event publisher)`. All behind interfaces so the stack is swappable.

## 9. Classes / folder (modular monolith)
```
app/parser/
  detection/   (magic, container, content-sniff, registry)
  loaders/     (pdf, docx, xlsx, csv, json, xml, html, md, txt, fhir, image)
  extractors/  (text, layout, ocr, table, image_meta, metadata, annotations)
  dom/         (models, builder, reading_order)
  storage/     (filesystem, s3 stub)
  jobs/        (queue, worker)
  ports.py ,  config.py
```

## 10. Database schema (MVP)
- `documents` (id, tenant, filename, sha256, size, detected_type mime, declared_ext, parser_version, status, model_version)
- `dom_versions` (document_id, version, root_json ref, store_path, checksum)
- `parse_events` (doc_id, parser_version, status, latency).
(Postgres with JSONB DOM cell later; MVP storage abstraction with local default; tables also as Parquet.)

## 11. Events published
- `document.parsed.v1` (next: normalizer)
- `document.parse_failed` (DLQ/monitor)

## 12. Events consumed
- `document.uploaded` (from Ingestion gateway)

## 13. Dependencies
- Runtime: Python, PyMuPDF (pdf), (likely pdfplumber for tables), RapidOCR (on-prem OCR), OpenPYXL/unstructured-lite for office, fastavro? → RapidOCR/images, pydantic. Later Postgres/object store.
- No GPU required in MVP (OCR CPU; inference elsewhere).

## 14. Configuration (versioned)
- Detector weights, OCR engine + lang, table heuristics knobs, DOM schema_version, chunk budget NOT here.

## 15. Error handling
- Distinct error codes (`UNSUPPORTED_TYPE, CORRUPT_FILE, OCR_ZERO, TIMEOUT, ZIP_BOMB`), categorize, never throw raw. Partial results preserved (what succeeded is kept, flagged by confidence).

## 16. Retry strategy
- Idempotent jobs keyed by `sha256`+version. Transient → retry w/ backoff; permanent → DLQ + alert.

## 17. Monitoring / 18. Metrics
- `docs_parsed`, `parse_latency_ms` p50/95/99, `parse_failures`, `ocr_confidence_histogram`, `extractor_error_rate`, `bytes_in`, per-format breakdown, queue depth.
- Structured logs: `events.jsonl` per doc (provenance+timing).

## 19. Security
- Virus/magic validation at ingestion; no raw bytes into prompts; path-traversal guard; tenant-isolated object-store keys; PII NOT stored in logs; limits on file size.

## 20. Performance
- One read; batching at extractor level; memory-mmap large PDFs; streams for huge logs; worker pool concurrency.

## 21. Scaling strategy
- Modular monolith + `ParserWorkers` pool first. Extract only OCR/image as a heavy lane when it dominates. Deterministic, idempotent ⇒ safe parallelism.

## 22. Testing strategy
- Fixture corpus per format (realistic: pdf+scan+docx+csv+html+fhir-json); golden DOM snapshots; char/token + structure overlap vs human-labeled ground truth; detection tests (extension-mangle/spoof); idempotency tests (rerun = same DOM); deterministic; unit + golden integration; quick CI.

## 23. Future improvements (order)
1) Add DICOM handler (extract images+metadata only). 2) Better table grid via model when heuristics fail. 3) LayoutLM/Donut-class layout model for hard scans. 4) Parquet table export. 5) Open the extractor plugin registry for customers.

## 24. Obs
Grafana-style dashboards + alerts on failure/degradation; every run's reportable from `events`.

## 25. Extensibility
- New format = new `Loader` + (extractors reuse) building the same DOM. No downstream change. DICOM, CAD, markdown-rich future.

## Failures of naive approaches this avoids
- regex dir on `Tj` (no layout), reading a doc twice per engine, hardcoding medical section names into chunking (healthcare-specific — explicitly rejected in SYN4), treating "validate" as "found", ignoring images in MVP, no lineage/versioning.

## What the parser must activate for the platform later
- Its DOM becomes the single feed to KG + validation. Extra care to keep it *faithful & fallible*: lossless provenance, confidence per block, and a record that survives reprojection.

---

**Gate to coding:** confirm stack (#1-#7 in `/project_memory/questions.md`). Until then this spec is §-correct but stack-open.