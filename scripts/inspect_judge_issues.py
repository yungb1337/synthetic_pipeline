import json
from collections import defaultdict
from pathlib import Path

judgment_dir = Path("checkpoints/run/run-2026-09-04-parser-reliability/judgment")
all_files = sorted(judgment_dir.glob("d-*.json"))

issues_by_surface = defaultdict(list)
verdicts = defaultdict(int)

for jf in all_files:
    data = json.loads(jf.read_text(encoding="utf-8"))
    v_data = data.get("verdict", {})
    if isinstance(v_data, dict):
        v = v_data.get("verdict", "UNKNOWN")
        issues = v_data.get("issues", [])
        notes = v_data.get("notes", "")
        metrics = v_data.get("metrics", {})
    else:
        v = str(v_data)
        issues = data.get("issues", [])
        notes = data.get("notes", "")
        metrics = data.get("scores", {})

    verdicts[v] += 1
    doc_id = data.get("doc_id", jf.stem)

    for iss in issues:
        surface = iss.get("surface", "other")
        sev = iss.get("severity", "minor")
        detail = iss.get("detail") or iss.get("description", "")
        sug = iss.get("suggestion", "")
        issues_by_surface[surface].append(
            {
                "doc_id": doc_id,
                "file": jf.name,
                "verdict": v,
                "severity": sev,
                "detail": detail,
                "suggestion": sug,
                "metrics": metrics,
                "notes": notes,
            }
        )

out_path = Path(
    "checkpoints/run/run-2026-09-04-parser-reliability/reports/judge_issues_analysis.txt"
)
with out_path.open("w", encoding="utf-8") as f:
    f.write(f"Total judged files: {len(all_files)}\n")
    f.write(f"Verdict counts: {dict(verdicts)}\n\n")
    f.write("=== ALL ISSUES BY SURFACE ===\n\n")
    for surface, items in sorted(issues_by_surface.items()):
        f.write(
            "======================================================================\n"
        )
        f.write(f"Surface: {surface} ({len(items)} issues)\n")
        f.write(
            "======================================================================\n"
        )
        for it in items:
            f.write(f"[{it['doc_id']}] [{it['severity']}] Verdict: {it['verdict']}\n")
            f.write(f"  Detail: {it['detail']}\n")
            if it["suggestion"]:
                f.write(f"  Suggestion: {it['suggestion']}\n")
            if it["notes"]:
                f.write(f"  Notes: {it['notes']}\n")
            f.write("\n")

print(f"Analysis written to {out_path}")
