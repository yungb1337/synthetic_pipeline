"""Remote Docling engine (ADR-013, Architecture E / B2).

Decouples the memory-heavy Docling layout/table inference into a dedicated
service or container fleet. The parser orchestrator runs lightweight
(native PDF, assembler, normalizer) while sending heavy page jobs via HTTP/RPC
to one or more Docling service workers.
"""
from __future__ import annotations

import base64
import json
import os
import traceback
import urllib.error
import urllib.request
from typing import Optional

from ..config import ParserConfig
from ..page_result import PageResult, PageStatus
from .base import DOCLING, PageWorkItem


class RemoteDoclingEngine:
    """Dispatches PageWorkItem to a remote Docling service via HTTP POST."""

    route_band = DOCLING

    def __init__(self, config: ParserConfig, timeout: float = 300.0):
        self.config = config
        self.timeout = timeout
        url = getattr(config, "docling_service_url", "") or ""
        self.service_url = url.rstrip("/")

    def health_check(self) -> bool:
        """Probe the remote service /health endpoint."""
        if not self.service_url:
            return False
        try:
            req = urllib.request.Request(
                f"{self.service_url}/health",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("status") == "ok"
        except Exception:
            return False
        return False

    def process(self, item: PageWorkItem) -> PageResult:
        """Send item to remote Docling worker and return deserialized PageResult."""
        if not self.service_url:
            return PageResult(
                doc_id=item.doc_id,
                page_index=item.page_index,
                route=DOCLING,
                status=PageStatus.FAILED,
                errors=[{
                    "page_no": item.page_index + 1,
                    "category": "remote_docling",
                    "message": "docling_service_url is not configured",
                }],
                source_hash=item.source_hash,
            )

        endpoint = f"{self.service_url}/process_page"
        payload: dict = {
            "doc_id": item.doc_id,
            "source_hash": item.source_hash,
            "src_path": item.src_path,
            "page_index": item.page_index,
            "route": item.route or DOCLING,
            "models_dir": item.models_dir or self.config.docling_models_dir,
            "ocr_enabled": item.ocr_enabled,
            "attempt": item.attempt,
            "docling_table_mode": item.docling_table_mode or self.config.docling_table_mode,
            "docling_ocr": item.docling_ocr if item.docling_ocr is not None else self.config.docling_ocr,
        }

        # If source path does not exist on remote or file is small/local, include base64 data
        if item.src_path and os.path.exists(item.src_path):
            try:
                # Include file bytes if under 50MB for self-contained remote execution
                if os.path.getsize(item.src_path) < 50 * 1024 * 1024:
                    with open(item.src_path, "rb") as fh:
                        payload["data_b64"] = base64.b64encode(fh.read()).decode("ascii")
            except Exception:
                pass

        data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                res_dict = json.loads(body)
                return PageResult.from_dict(res_dict)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            return PageResult(
                doc_id=item.doc_id,
                page_index=item.page_index,
                route=DOCLING,
                status=PageStatus.FAILED,
                errors=[{
                    "page_no": item.page_index + 1,
                    "category": "remote_docling_http_error",
                    "message": f"HTTP {e.code}: {e.reason} - {err_body}",
                }],
                source_hash=item.source_hash,
            )
        except Exception as exc:
            return PageResult(
                doc_id=item.doc_id,
                page_index=item.page_index,
                route=DOCLING,
                status=PageStatus.FAILED,
                errors=[{
                    "page_no": item.page_index + 1,
                    "category": "remote_docling_connection",
                    "message": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }],
                source_hash=item.source_hash,
            )
