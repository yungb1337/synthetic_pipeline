"""Alpaca Instruction Format Exporter.

Serializes synthetic records to standard Stanford Alpaca JSON format.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.synthetic.exporter.protocol import ExportFormat
from app.synthetic.schema import SyntheticRecord


class AlpacaExporter:
    """Exports records to Stanford Alpaca JSON format."""

    @property
    def format_name(self) -> str:
        return "alpaca"

    def export(self, records: list[SyntheticRecord], output_path: Path) -> Path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        alpaca_entries = []
        for rec in records:
            trace = rec.reasoning_trace.strip()
            if not trace.startswith("<think>"):
                trace = f"<think>\n{trace}\n</think>"

            full_output = f"{trace}\n\n{rec.target_response.strip()}"
            alpaca_entries.append(
                {
                    "instruction": rec.instruction.strip(),
                    "input": "",
                    "output": full_output,
                }
            )

        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(alpaca_entries, f, indent=2, ensure_ascii=False)

        return out_p
