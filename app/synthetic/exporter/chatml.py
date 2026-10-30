"""ChatML Exporter with <think> CoT Tags.

Serializes synthetic records to JSONL format compatible with OpenAI, Qwen,
and DeepSeek ChatML instruction fine-tuning pipelines.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.synthetic.exporter.protocol import ExportFormat
from app.synthetic.schema import SyntheticRecord


class ChatMLExporter:
    """Exports records to ChatML JSONL format."""

    @property
    def format_name(self) -> str:
        return "chatml"

    def export(self, records: list[SyntheticRecord], output_path: Path) -> Path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        system_msg = (
            "You are an expert clinical pharmacologist. Analyze medical cases with rigorous "
            "step-by-step reasoning enclosed in <think> tags before providing the final answer."
        )

        with open(out_p, "w", encoding="utf-8") as f:
            for rec in records:
                # Ensure think tags are properly structured
                trace = rec.reasoning_trace.strip()
                if not trace.startswith("<think>"):
                    trace = f"<think>\n{trace}\n</think>"

                assistant_content = f"{trace}\n\n{rec.target_response.strip()}"

                entry = {
                    "id": rec.record_id,
                    "dataset_version": rec.dataset_version,
                    "task_type": rec.task_type,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": rec.instruction.strip()},
                        {"role": "assistant", "content": assistant_content},
                    ],
                    "provenance": [p.model_dump() for p in rec.provenance],
                    "quality_metadata": rec.quality_metadata.model_dump(),
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return out_p
