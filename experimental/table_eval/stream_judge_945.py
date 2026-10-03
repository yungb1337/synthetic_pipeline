"""Concurrent stream judge for Corpus 945 evaluation.
Runs LLM Judge concurrently on newly generated canonical DOMs for Corpus 945.
"""

from __future__ import annotations

import concurrent.futures
import json
import sys
import time
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from experimental.table_eval.judge_evaluator import TableBenchmarkJudgeEvaluator

DOM_DIR = (
    WORKSPACE_ROOT
    / "artifacts"
    / "table_eval"
    / "corpus_945_p003"
    / "normalized_output"
)
LOGS_DIR = WORKSPACE_ROOT / "artifacts" / "table_eval" / "corpus_945_p003" / "logs"
SOURCES_DIR = (
    WORKSPACE_ROOT
    / "checkpoints"
    / "run"
    / "run-2026-09-14-full-corpus"
    / "parsed"
    / "raw"
)

LOGS_DIR.mkdir(parents=True, exist_ok=True)


def load_doc_mapping() -> dict[str, Path]:
    """Maps doc_id -> source pdf_path."""
    doc_map: dict[str, Path] = {}
    for p in SOURCES_DIR.glob("*.pdf"):
        doc_id = f"d-{p.stem[:16]}"
        doc_map[doc_id] = p
    return doc_map


def _judge_task(task: dict[str, Any]) -> dict[str, Any]:
    doc_id = task["doc_id"]
    pdf_path = task["pdf_path"]
    dom_path = task["dom_path"]
    out_verdict_path = task["out_verdict_path"]
    pacing = task.get("pacing", 1.0)
    model = task.get("model", "gemini-3.5-flash-lite")

    if out_verdict_path.exists():
        try:
            data = json.loads(out_verdict_path.read_text(encoding="utf-8"))
            return {"doc_id": doc_id, "data": data, "cached": True}
        except Exception:
            pass

    judge = TableBenchmarkJudgeEvaluator(model=model, pacing_seconds=pacing)
    out_verdict_path.parent.mkdir(parents=True, exist_ok=True)
    res = judge.evaluate_doc(pdf_path, dom_path, out_verdict_path)
    return {"doc_id": doc_id, "data": res, "cached": False}


def run_stream_judge_945(
    max_workers: int = 4, pacing: float = 1.0, model: str = "gemini-3.5-flash-lite"
):
    doc_map = load_doc_mapping()
    print(f"[STREAM JUDGE 945] Loaded {len(doc_map)} document mappings.")
    print(
        f"[STREAM JUDGE 945] Concurrency: {max_workers} workers | Model: {model} | Pacing: {pacing}s"
    )

    total_judged = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        while True:
            dom_files = sorted(DOM_DIR.glob("*.parsed.v1.docJSON"))
            pending_tasks = []

            for df in dom_files:
                doc_id = df.name.split(".parsed.")[0]
                out_v = LOGS_DIR / f"{doc_id}.verdict.json"
                if not out_v.exists():
                    pdf_p = doc_map.get(doc_id)
                    if pdf_p and pdf_p.exists():
                        pending_tasks.append(
                            {
                                "doc_id": doc_id,
                                "pdf_path": pdf_p,
                                "dom_path": df,
                                "out_verdict_path": out_v,
                                "pacing": pacing,
                                "model": model,
                            }
                        )

            if pending_tasks:
                print(
                    f"\n[STREAM JUDGE 945] Found {len(pending_tasks)} pending DOMs ready for judging..."
                )
                future_to_doc = {
                    executor.submit(_judge_task, task): task for task in pending_tasks
                }
                for future in concurrent.futures.as_completed(future_to_doc):
                    task = future_to_doc[future]
                    total_judged += 1
                    try:
                        res = future.result()
                        j_data = res.get("data", {})
                        v_status = j_data.get("verdict_status") or (
                            j_data.get("verdict")
                            if isinstance(j_data.get("verdict"), str)
                            else "PASS"
                        )
                        metrics = j_data.get("metrics", {})
                        t_score = metrics.get("tables", 0.0)
                        f_score = metrics.get("fidelity", 0.0)
                        cached = " (cached)" if res.get("cached") else ""
                        print(
                            f"[{total_judged:3d}] {task['doc_id']}{cached} -> {v_status} (Tables: {t_score:.2f}, Fidelity: {f_score:.2f})",
                            flush=True,
                        )
                    except Exception as exc:
                        print(
                            f"[{total_judged:3d}] {task['doc_id']} -> ERROR: {exc}",
                            flush=True,
                        )

            existing_verdicts = list(LOGS_DIR.glob("*.verdict.json"))
            if len(existing_verdicts) >= 945:
                print(
                    f"[STREAM JUDGE 945] All {len(existing_verdicts)} documents judged! Exiting."
                )
                break

            time.sleep(5)


if __name__ == "__main__":
    run_stream_judge_945()
