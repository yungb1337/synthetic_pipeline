"""HuggingFace Parquet Dataset Exporter.

Serializes full SyntheticRecord objects into columnar Parquet datasets
with embedded grounding subgraphs, provenance pointers, and quality certifications.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from app.synthetic.exporter.protocol import ExportFormat
from app.synthetic.schema import SyntheticRecord


class HuggingFaceExporter:
    """Exports synthetic records to HuggingFace Parquet format."""

    @property
    def format_name(self) -> str:
        return "parquet"

    def export(self, records: list[SyntheticRecord], output_path: Path) -> Path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        if not str(out_p).endswith(".parquet"):
            out_p = out_p.with_suffix(".parquet")

        rows: list[dict[str, Any]] = []
        for rec in records:
            rows.append(
                {
                    "record_id": rec.record_id,
                    "dataset_version": rec.dataset_version,
                    "task_type": rec.task_type,
                    "domain": rec.domain,
                    "instruction": rec.instruction,
                    "reasoning_trace": rec.reasoning_trace,
                    "target_response": rec.target_response,
                    "negative_response": rec.negative_response or "",
                    "grounding_subgraph": json.dumps(rec.grounding_subgraph.model_dump()),
                    "provenance": json.dumps([p.model_dump() for p in rec.provenance]),
                    "quality_metadata": json.dumps(rec.quality_metadata.model_dump()),
                }
            )

        if not rows:
            rows.append(
                {
                    "record_id": "dummy",
                    "dataset_version": "1.0",
                    "task_type": "dummy",
                    "domain": "pharma",
                    "instruction": "dummy",
                    "reasoning_trace": "dummy",
                    "target_response": "dummy",
                    "negative_response": "",
                    "grounding_subgraph": "{}",
                    "provenance": "[]",
                    "quality_metadata": "{}",
                }
            )

        tuples = []
        for r in rows:
            tuples.append(
                (
                    r["record_id"],
                    r["dataset_version"],
                    r["task_type"],
                    r["domain"],
                    r["instruction"],
                    r["reasoning_trace"],
                    r["target_response"],
                    r["negative_response"],
                    r["grounding_subgraph"],
                    r["provenance"],
                    r["quality_metadata"],
                )
            )

        con = duckdb.connect()
        con.execute(
            """
            CREATE TABLE hf_export (
                record_id VARCHAR,
                dataset_version VARCHAR,
                task_type VARCHAR,
                domain VARCHAR,
                instruction VARCHAR,
                reasoning_trace VARCHAR,
                target_response VARCHAR,
                negative_response VARCHAR,
                grounding_subgraph VARCHAR,
                provenance VARCHAR,
                quality_metadata VARCHAR
            )
            """
        )
        con.executemany("INSERT INTO hf_export VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuples)
        escaped_path = str(out_p).replace("\\", "/")
        con.execute(f"COPY hf_export TO '{escaped_path}' (FORMAT PARQUET)")
        con.close()

        return out_p
