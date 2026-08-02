---
name: project-vision-medfactory
description: An anchored, durable statement of what this project is — an enterprise trust platform whose first vertical is healthcare synthetic data
metadata:
  type: project
---

# Master Context — PROJECT: Synthetic Data Factory ("build a verifiable knowledge platform")

**Last updated:** 2026-08-02 (session: post-read-all-sources)

## What we are building (one sentence)
A platform that transforms an enterprise's proprietary knowledge into **privacy-preserving, explainable, continuously-improving, validated** synthetic datasets — where **Trust & verification is the product**, not the synthetic records themselves.

## Why it exists (from the sources)
- High-quality / labeled / rare-event / privacy-safe data is scarce (`SYN3`). Synthetic data is the *tool*, not the product — customers buy "better AI + privacy + compliance + accuracy."
- Working name is **MedFactory AI** (healthcare-first). First vertical: healthcare. Future: finance, legal, manufacturing, insurance, government.

## First mover's thinking (user, verbatim intent)
> "We are creating trust ... we are providing Trust instead of just creating synthetic datasets."
"Start with one domain (healthcare), but do NOT bake medical assumptions into the core."

## The central loop the platform must serve (from SYN1)
Documents → Parse → Clean/Normalize → Entity+Rel Extract → **Knowledge Graph (source of truth)** → Prompt Builder (KG + constraints) → LLM (constrained generator only) → instance → Multi-stage validation (`Valid / Unknown / Contradiction`) → trusted dataset v1..vN → deployment → monitoring → weakness analysis → generate *only missing* cases → back to KG/prompt.

## Guardrails / principles the user explicitly gave
- Never blindly agree. Challenge, compare, trade-offs, recommend.
- Distinguish **Fact | Research | Inference | Recommendation**.
- If missing → say so, don't invent.
- **Modular monolith** first; Clean Architecture; DDD where apt; event-driven; idempotent jobs; versioned APIs; observable. No microservices without proof.
- Document everything; preserve reasoning; keep checkpoints.

**Sources read (extracted text):** SYN1–SYN4 (ChatGPT conversation exports), + 4 academic PDFs (pending subagent synthesis) + Peter Lee synthetic-data-ai essay. Raw text copied under `_research_sources/`.

**See also:** [[architecture-decisions]], [[reading-notes]], [[module-status]], [[parser-scope]].