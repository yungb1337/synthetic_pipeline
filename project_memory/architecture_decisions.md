---
name: parser-scope-and-decisions
description: Accepted scoping and key decisions for the first module (the parser / document extraction pipeline), derived from SYN1-4
metadata:
  type: project
---

# Parser Module — Accepted Decisions & Scope (Module #1)

**Decision base:** SYN4 (the conversation that redesigned the pipeline as a single-pass extraction → Canonical Document Object), SYSTEMAL from the project brief (modular monolith, event-driven, idempotent, observable, no premature microservices).

## The one sentence
The parser = **Document Extraction Pipeline**: detect file type → load once → run parallel extractors (text / layout / OCR / tables / images / metadata / coordinates / annotations) → build a canonical **Document Object Model** → serialize + persist. Output must be parser-independent so all downstream (normalize, chunk, embed, KG) never touch the original file.

## Accepted decisions (Fact — from SYN1-4)
1. **Canonical DOM object** between parse and everything downstream (`Document → Metadata, Pages → Blocks → Paragraphs/Table/Image/Annotation`, coordinates, references, version).
2. **Layout is an extractor inside the pipeline**, not a separate engine. Do not read the file twice.
3. **Reading Order Graph** = in-memory directed graph (`block → next block`). No Neo4j.
4. **OCR is on-demand** by detected type (scanned PDF / image) and recorded (deskew → denoise → contrast → OCR model → text + confidence).
5. **Tables are first-class** (grid, rows, columns, headers, cell types); loss without them.
6. **Images are extracted + stored + referenced**, NOT analyzed in MVP (GPU + latency).
7. **Modular monolith** (FastAPI backend + module packages + worker pools). Modules = responsibilities, not network boundaries.
8. **Idempotent** jobs; **versioned** outputs (`parser:vN`), full lineage retained; **immutable object storage** for raw + parsed.

## In scope for THIS milestone
- File type detection (magic bytes + container + content sniff + extension last).
- Loaders: PDF (text+layout+tables+images), DOCX, XLSX/CSV/TSV, HTML/Markdown, JSON/XML/Plaintext, scanned-image (OCR), FHIR-JSON (first healthcare-specific map).
- Canonical Document Builder + JSON serialization + storage-write.
- Tests (deterministic fixtures per format), observability (per-parser latency/error/confidence), config.

## Out of scope NOW (later modules keep pipeline unchanged)
Text **normalization**, **semantic chunking**, **embeddings**, entity/rel extraction, KG, generation, validation, multi-tenancy around it, delivery APIs, dashboards.

## Open questions blocking a final DB/schema
See [[questions]]. Confirm tech stack before coding (AskUserQuestion was issued in-session).

## Known contradiction to surface (do not bury)
SYN3 (later ChatGPT) argued KG is overhyped and to defer it; SYN1/SYN2 treat KG as the trust spine/operating system. Resolution is deferred to the Knowledge Platform phase — does NOT block the parser.