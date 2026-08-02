---
name: open-questions
description: Open decisions that block progress, tracked so nothing is invented
metadata:
  type: project
---

# Open Questions

## Parser-scoped (need answers before code)
1. **Language/runtime for the monolith** — Python (matches SYN4 FastAPI + the ecosystem: PyMuPDF, pdfplumber, Tesseract/PaddleOCR, pydantic). Any constraint? (Recommend Python.)
2. **PDF backend** — `PyMuPDF` vs `pdfplumber` vs `pypdfium2` vs a hybrid. (Recommend PyMuPDF for speed+layout; pdfplumber for table heuristics.)
3. **OCR engine** — Tesseract (CPU, free) vs PaddleOCR/`RapidOCR` (better accuracy, ML) vs cloud (privacy risk). Recommend `RapidOCR`/PaddleOCR on-prem to honor "no raw data leaves hospital."
4. **Storage for MV** — local filesystem verses object-store dummy? DB (Postgres JSONB later). For MVP: a filesystem/object-store abstraction with a local default.
5. **Canonical DOM serialization** — JSON (schema) + optional Parquet for tables. Confirm JSON for v1.
6. **Job/workflow threading** — Celery+Redis vs plain worker threads/Asyncerrio for MVP (monolith). Recommend deferred heavy framework; a simple queue first.
7. **Versioning approach** — git-style vs datetime/`parser_version`. Recommend `doc + parser_version + sha`.

## Platform-level (NOT blocking parser but recorded)
8. **KG as operating system (SYN1/2) vs "defer/avoid KG, hybrid retrieval" (SYN3)** — must be deliberately decided at KG-phase. Do not interpret now.
9. Deployment posture: on-prem/private-cloud per customer (the sources strongly imply this for healthcare) — affects OCR/storage/inference choices early.
10. Multilingual? Regional coding (ICD-10 vs regional)? Affects ontology + OCR.
11. Do hospitals share EHR tables via FHIR (structured) mostly? Determines parser priority.

## Process
Use AskUserQuestion for #1–#7 (they change the codebase and are not derivable from the docs).