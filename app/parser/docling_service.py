"""Docling Microservice Server (Architecture E / B2).

Runs a standalone HTTP service that receives single-page Docling extraction
requests and processes them out-of-process from the parser orchestrator.

Usage:
    python -m app.parser.docling_service --port 8001 --host 0.0.0.0
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .config import ParserConfig, default_config
from .engines.base import PageWorkItem
from .engines.heavy_docling import HeavyDoclingEngine
from .loaders import docling_loader
from .page_result import PageResult, PageStatus
from .utils import get_logger

logger = get_logger(__name__)


class DoclingRequestHandler(BaseHTTPRequestHandler):
    config: ParserConfig = default_config()
    _engine: Optional[HeavyDoclingEngine] = None

    @classmethod
    def get_engine(cls) -> HeavyDoclingEngine:
        if cls._engine is None:
            cls._engine = HeavyDoclingEngine(cls.config)
        return cls._engine

    def do_GET(self) -> None:
        if self.path == "/health" or self.path == "/":
            payload = {
                "status": "ok",
                "service": "docling_service",
                "docling_package": docling_loader.engine_name(),
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/ready":
            avail = docling_loader.engine_available()
            payload = {
                "status": "ok" if avail else "degraded",
                "service": "docling_service",
                "docling_available": avail,
                "docling_package": docling_loader.engine_name(),
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200 if avail else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/process_page" or self.path == "/process":
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error": "empty body"}')
                return

            try:
                body = self.rfile.read(content_length).decode("utf-8")
                req_data = json.loads(body)
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"invalid json: {e}"}).encode("utf-8"))
                return

            temp_file = None
            src_path = req_data.get("src_path", "")
            if req_data.get("data_b64") and (not src_path or not os.path.exists(src_path)):
                # Materialize uploaded bytes to temp file
                try:
                    raw_bytes = base64.b64decode(req_data["data_b64"])
                    tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                    tf.write(raw_bytes)
                    tf.flush()
                    tf.close()
                    src_path = tf.name
                    temp_file = tf.name
                except Exception as e:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": f"failed to decode data_b64: {e}"}).encode("utf-8"))
                    return

            try:
                item = PageWorkItem(
                    doc_id=req_data.get("doc_id", ""),
                    source_hash=req_data.get("source_hash", ""),
                    src_path=src_path,
                    page_index=int(req_data.get("page_index", 0)),
                    route=req_data.get("route", "docling"),
                    models_dir=req_data.get("models_dir", ""),
                    ocr_enabled=bool(req_data.get("ocr_enabled", True)),
                    attempt=int(req_data.get("attempt", 0)),
                    docling_table_mode=req_data.get("docling_table_mode", ""),
                    docling_ocr=bool(req_data.get("docling_ocr", False)),
                )
                engine = self.get_engine()
                res: PageResult = engine.process(item)
                res_dict = res.to_dict()

                resp_body = json.dumps(res_dict, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            finally:
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        # Keep service quiet during batch processing unless errors occur
        pass


def create_server(host: str = "127.0.0.1", port: int = 8001,
                  config: ParserConfig | None = None) -> ThreadingHTTPServer:
    handler = DoclingRequestHandler
    if config is not None:
        handler.config = config
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Docling Microservice Server (Architecture E / B2)")
    ap.add_argument("--host", default="0.0.0.0", help="bind host (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=8001, help="bind port (default 8001)")
    args = ap.parse_args(argv)

    print(f"Starting Docling service on {args.host}:{args.port}...")
    server = create_server(host=args.host, port=args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Docling service...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
