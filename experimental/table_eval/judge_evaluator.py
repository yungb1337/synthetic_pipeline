"""Bridge to run the official scripts/llm_judge.py against benchmark canonical DOMs.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
PYTHON_EXE = str(Path(sys.executable).resolve())
JUDGE_SCRIPT = ROOT_DIR / "scripts" / "llm_judge.py"


class TableBenchmarkJudgeEvaluator:
    """Invokes scripts/llm_judge.py with strict rate-limiting and structured verdict parsing."""

    def __init__(
        self,
        model: str = "gemini-3.5-flash-lite",
        max_chars: int = 12000,
        pacing_seconds: float = 3.0,
    ):
        self.model = model
        self.max_chars = max_chars
        self.pacing_seconds = pacing_seconds

    def evaluate_doc(
        self,
        pdf_path: Path,
        dom_json_path: Path,
        output_verdict_path: Path,
    ) -> dict[str, Any] | None:
        """Executes llm_judge.py as a subprocess to preserve full sandbox and memory isolation."""
        cmd = [
            PYTHON_EXE,
            str(JUDGE_SCRIPT),
            "--pdf", str(pdf_path),
            "--dom", str(dom_json_path),
            "--out", str(output_verdict_path),
            "--model", self.model,
            "--max-chars", str(self.max_chars),
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
            time.sleep(self.pacing_seconds)

            if output_verdict_path.exists():
                data = json.loads(output_verdict_path.read_text(encoding="utf-8"))
                # If nested under "verdict" object (standard scripts/llm_judge.py format)
                if "verdict" in data and isinstance(data["verdict"], dict):
                    v_obj = data["verdict"]
                    if "metrics" in v_obj:
                        data["metrics"] = v_obj["metrics"]
                    if "verdict" in v_obj:
                        data["verdict_status"] = v_obj["verdict"]
                return data
            else:
                return {
                    "verdict": "FAIL",
                    "metrics": {
                        "completeness": 0.0,
                        "fidelity": 0.0,
                        "structure": 0.0,
                        "tables": 0.0,
                        "references": 0.0,
                        "scans_ocr": 0.0,
                    },
                    "error": f"Judge script exited with code {res.returncode}. Stderr: {res.stderr[:300]}",
                }
        except Exception as exc:
            return {
                "verdict": "FAIL",
                "metrics": {
                    "completeness": 0.0,
                    "fidelity": 0.0,
                    "structure": 0.0,
                    "tables": 0.0,
                    "references": 0.0,
                    "scans_ocr": 0.0,
                },
                "error": str(exc),
            }
