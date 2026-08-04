# Org Gate Protocol — Hard Gates for the Autonomous Engineering Organization

Single source of truth for how the 8-agent organization executes a run. **Agents: read this before acting. Orchestrator: enforce it.**

## The pipeline

```
Research ──► Architecture ──► Implementation ──► Engineer ──► Architecture ──► Quality & ──► Knowledge
(Gate 1)     (+ trade-off     Plan (Gate 3)      (Gate 4)     Review (Gate 5)    Perf Review    Curator
             review, Gate 2)                                                  (Gate 6)        checkpoint
                                                                                                (Gate 7)
```

## Roles
| Role | Agent | Gate | Artifact |
|---|---|---|---|
| Research Lead | `research-lead` | 1 | `checkpoints/run/<run_id>/research.md` → `RESEARCH: COMPLETE` |
| Chief Architect | `chief-architect` | 2 | `checkpoints/run/<run_id>/architecture.md` → `ARCHITECTURE: APPROVED` |
| Technical Planner | `technical-planner` | 3 | `checkpoints/run/<run_id>/implementation-plan.md` → `PLAN: READY` |
| Implementation Engineer | `implementation-engineer` | 4 | code in `app/` + `engineer-report.md` |
| Architecture Reviewer | `architecture-reviewer` | 5 | `checkpoints/run/<run_id>/reviews/architecture.md` → `VERDICT: PASS\|FAIL` |
| Quality & Performance Reviewer | `quality-reviewer` | 6 | `checkpoints/run/<run_id>/reviews/quality.md` → `VERDICT: PASS\|FAIL` |
| Knowledge Curator | `knowledge-curator` | 7 | updated `project_memory/` + `checkpoints/run/<run_id>/checkpoint.md` |
| Project Orchestrator | `project-orchestrator` | — | conducts all gates; `final-report.md` |

The run id and objective come from `project_memory/active_objective.md`. Every gate artifact lives under `checkpoints/run/<run_id>/`.

## Hard-gate rules (no exceptions)

1. **No phase starts until the previous gate PASSes.** The orchestrator reads each artifact's verdict line before spawning the next agent — verify, don't trust.
2. **FAIL always routes back.** A review `FAIL` sends the reviewer's ordered issue list to the Implementation Engineer, who fixes it, then both reviewers re-run. Nothing is marked done while a reviewer `FAIL`s.
3. **Reviewers are read-only.** Architecture Reviewer has no edit tools; Quality Reviewer's Bash is read-only. They cannot silently change code — they can only report.
4. **Bound the loop.** A fix loop runs at most 3 rounds; if it still `FAIL`s, the orchestrator escalates to the user with the remaining issues and the reviewers' reports.
5. **Escalate only irreversible decisions.** Deleting data, changing stack foundations, breaking an ADR. Everything else the orchestrator decides and records the reasoning for.
6. **Append, never destroy.** `project_memory/`, `docs/`, and `checkpoints/` only ever gain content. The Knowledge Curator marks stale items rather than deleting them.
7. **Tests are part of done.** A run is complete only when both reviewers `PASS` **and** `.venv/Scripts/python.exe -m pytest tests/ -q` is green.

## Two drive modes

- **In-session:** `/dev-team` — the main session acts as the Project Orchestrator (playbook in `.claude/agents/project-orchestrator.md`) and runs the full pipeline, observable, interruptible.
- **Background (fire-and-forget):** the `audit` workflow (`.claude/workflows/audit.js`) runs the org autonomously and writes `checkpoints/run/<run_id>/final-report.md`.

Both read the same run brief and enforce the same gates.
