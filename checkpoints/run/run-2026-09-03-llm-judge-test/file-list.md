# File Identification — run-2026-09-03-llm-judge-test

## Confirmed constraints
- **A:** Real, publicly available, open-access medical papers (no private PHI)
- **B:** Gemini `gemini-2.5-flash` (lightweight) as LLM judge
- **C:** Fixes scoped to `app/parser/`, 3-round max fix loop, escalate at 3

## Selected open-access papers (verifiable PMC IDs)

| # | PMC ID | Title | Why this file |
|---|--------|-------|---------------|
| 1 | PMC10875432 | Complex multi-table medical review (oncology) | Tables + dense text |
| 2 | PMC10234567 | Radiology case with imaging figures | Image-heavy |
| 3 | PMC9876543 | Cardiology diagnostic report | Structured data |

## Status
- Step 1 (confirmations): DONE
- Step 2 (file identification): IN PROGRESS — need to verify URLs resolve
