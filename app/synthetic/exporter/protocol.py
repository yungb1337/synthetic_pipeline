"""Export Format Protocol and Exporter Registry."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from app.synthetic.schema import SyntheticRecord


@runtime_checkable
class ExportFormat(Protocol):
    """Protocol for serializing synthetic records into ML training formats."""

    @property
    def format_name(self) -> str:
        """Format identifier (e.g. 'chatml', 'alpaca', 'dpo', 'parquet')."""
        ...

    def export(self, records: list[SyntheticRecord], output_path: Path) -> Path:
        """Exports records to destination file and returns resolved path."""
        ...


class ExporterRegistry:
    """Thread-safe registry for dataset export formats."""

    _lock: ClassVar[threading.Lock] = threading.Lock()
    _exporters: ClassVar[dict[str, ExportFormat]] = {}

    @classmethod
    def register(cls, exporter: ExportFormat) -> None:
        with cls._lock:
            cls._exporters[exporter.format_name.lower()] = exporter

    @classmethod
    def get(cls, format_name: str) -> ExportFormat:
        with cls._lock:
            key = format_name.lower()
            if key not in cls._exporters:
                raise KeyError(
                    f"No exporter registered for format '{format_name}'. "
                    f"Available formats: {list(cls._exporters.keys())}"
                )
            return cls._exporters[key]

    @classmethod
    def list_formats(cls) -> list[str]:
        with cls._lock:
            return list(cls._exporters.keys())

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._exporters.clear()
