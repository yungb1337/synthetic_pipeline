"""DPO Preference Dataset Exporter.

Serializes Direct Preference Optimization (DPO) chosen/rejected pairs
into columnar Parquet datasets containing prompt, chosen, rejected, and provenance metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from app.synthetic.exporter.protocol import ExportFormat
from app.synthetic.schema import SyntheticRecord


class DPOArrowExporter:
    """Exports DPO preference pairs to columnar Parquet/Arrow format."""

    @property
    def format_name(self) -> str:
        return "dpo"

    def export(self, records: list[SyntheticRecord], output_path: Path) -> Path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        if not str(out_p).endswith(".parquet"):
            out_p = out_p.with_suffix(".parquet")

        rows: list[dict[str, Any]] = []
        for rec in records:
            trace = rec.reasoning_trace.strip()
            if not trace.startswith("<think>"):
                trace = f"<think>\n{trace}\n</think>"

            chosen_text = f"{trace}\n\n{rec.target_response.strip()}"
            rejected_text = rec.negative_response or "Uncalibrated recommendation lacking clinical qualifiers."

            meta = {
                "record_id": rec.record_id,
                "task_type": rec.task_type,
                "complexity_hops": rec.quality_metadata.complexity_hops,
                "evidence_entailment_score": rec.quality_metadata.evidence_entailment_score,
                "provenance_blocks": [p.block_id for p in rec.provenance],
            }

            rows.append(
                {
                    "prompt": rec.instruction.strip(),
                    "chosen": chosen_text,
                    "rejected": rejected_text,
                    "metadata": json.dumps(meta),
                }
            )

        if not rows:
            rows.append(
                {
                    "prompt": "dummy",
                    "chosen": "dummy",
                    "rejected": "dummy",
                    "metadata": "{}",
                }
            )

        tuples = []
        for r in rows:
            tuples.append((r["prompt"], r["chosen"], r["rejected"], r["metadata"]))

        con = duckdb.connect()
        con.execute(
            "CREATE TABLE dpo_export (prompt VARCHAR, chosen VARCHAR, rejected VARCHAR, metadata VARCHAR)"
        )
        con.executemany("INSERT INTO dpo_export VALUES (?, ?, ?, ?)", tuples)
        escaped_path = str(out_p).replace("\\", "/")
        con.execute(f"COPY dpo_export TO '{escaped_path}' (FORMAT PARQUET)")
        con.close()

        return out_p
