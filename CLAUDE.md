# CLAUDE.md — Synthetic Data Factory (MedFactory AI)

Standing brief auto-loaded by every agent (and the human). Keep this current.

## Mission (one sentence)
A platform that transforms an enterprise's proprietary knowledge into **privacy-preserving, explainable, continuously-improving, validated** synthetic datasets — where **Trust & verification is the product**, not the synthetic records themselves. First vertical: healthcare. Working name: **MedFactory AI**.

## How the org works
This repo runs as an autonomous engineering organization. The **Project Orchestrator** (`/dev-team` in-session, or the `audit` workflow in background) reads the run brief in `project_memory/active_objective.md` and drives the gate pipeline. Roles live in `.claude/agents/`; the pipeline and hard gates are in `docs/org-gate-protocol.md`. The Knowledge Curator keeps the blackboard (`project_memory/`) current.

## Repo layout (modular monolith)
- `app/parser/` — Module #1: document → canonical DOM (extraction pipeline)
- `app/normalizer/` — Module #2: DOM → clean DOM
- `app/processing/` — batch/scale execution (thousands→millions of docs)
- `app/embedding/` — batching-capable embedding seam (BGE-M3 1024-dim, GPU)
- `docs/` — design specs (parser, normalizer, scale-batch, universal engine, gate protocol)
- `project_memory/` — the shared blackboard (master context, ADRs, module status, questions, index)
- `checkpoints/` — durable checkpoints per milestone and per run (`checkpoints/run/<run_id>/`)
- `scripts/` — tooling (download_models, check_embedder, check_similarity)
- `_research_sources/` — extracted research source text (SYN1–4 + papers)

## Running tests / the pipeline
- venv python: Windows `.venv/Scripts/python.exe` · POSIX `.venv/bin/python`
- tests: `.venv/Scripts/python.exe -m pytest tests/ -q`
- end-to-end smoke driver: `.claude/skills/run-synthetic-data-factory/driver.py`
- embedding check: `scripts/check_embedder.py`
- code-duplication signal: `scripts/check_similarity.py`

## Guardrails (always)
- Never blindly agree. Challenge, compare, trade-offs, recommend.
- Distinguish **Fact | Research | Inference | Recommendation** — label them.
- If something is missing, say so; never invent.
- Modular monolith first; Clean Architecture; DDD where apt; event-driven; idempotent jobs; versioned APIs; observable. No microservices without proof.
- Document everything; preserve reasoning; **append, never destroy** (memory + ADRs).

## Pointers
- Master context (mission, guardrails): `project_memory/master_context.md`
- Architecture decisions (ADRs): `project_memory/architecture_decisions.md`
- Live module status: `project_memory/module_status.md`
- Open questions: `project_memory/questions.md`
- Gate protocol: `docs/org-gate-protocol.md`
- Current run brief: `project_memory/active_objective.md`
- Memory index: `project_memory/MEMORY.md`
