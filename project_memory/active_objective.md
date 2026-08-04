---
name: active-objective
description: The run brief — the objective and notes the autonomous organization executes next. Overwrite the body for each new run; never delete the file.
metadata:
  type: project
---

# Active Run Brief

> The Project Orchestrator (`/dev-team` in-session, or the `audit` workflow in background)
> reads this file at the start of a run. Replace the contents for a new run; keep this file.

## Run id
`run-2026-08-04-audit`

## Objective
Audit the existing modules — `app/parser`, `app/normalizer`, `app/processing`, `app/embedding` — and fix what the audit surfaces.

## Scope
- **Architecture alignment:** does the code match the specs in `docs/` and the ADRs in `project_memory/architecture_decisions.md`?
- **Code duplication:** run `scripts/check_similarity.py`; flag near-duplicate functions/blocks for consolidation.
- **Quality & performance:** SOLID, testability, maintainability, and anything that won't scale to millions of docs.

## Constraints
- No new features. Audit + fix only.
- Keep the modular-monolith boundaries intact.
- Respect the append-only memory rule.

## Definition of done
1. Architecture Reviewer and Quality & Performance Reviewer both emit `VERDICT: PASS`.
2. `.venv/Scripts/python.exe -m pytest tests/ -q` is green.
3. Knowledge Curator checkpoint written to `checkpoints/run/run-2026-08-04-audit/checkpoint.md`.

## Notes for the team
This is the org's first run — expect to also shake out the process itself. Record anything awkward in the checkpoint so the org improves on the next run.
