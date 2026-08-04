---
name: open-questions
description: Open decisions that block progress, tracked so nothing is invented
metadata:
  type: project
---

# Open Questions

## Parser-scoped (resolved)
1-7. Tech stack confirmed in-session: Python/FastAPI modular monolith, PyMuPDF, RapidOCR (on-prem), object-store abstraction w/ FS default, DOM JSON, job-worker layer. See architecture_decisions.md.

## RESOLVED (user decision, 2026-08-04)
**KG contradiction (SYN1/2 vs SYN3) — decided:** We MUST use the **Knowledge Graph as the grounded source of truth**; **Ontology is just as important as the KG** (keeps the graph sane as data grows). Embeddings/KG are complementary, not competing: KG = verified memory; embeddings = candidate retrieval to get unstructured text INTO the KG and to verify `Unknown`s; ontology = consistency.