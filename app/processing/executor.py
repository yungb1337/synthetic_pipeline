"""Parallel batch executor over a corpus.

Runs the deterministic Parse → Normalize pipeline across thousands→millions of
documents using a worker pool, with:
  * shared, warmed model engines (OCR preloaded once),
  * retries with backoff on transient failures,
  * an in-memory + persisted "done" manifest so progress survives crashes and
    re-runs are incremental,
  * a BatchReport with per-format breakdown for monitoring.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

from ..parser import ocr as parser_ocr
from ..parser.events import EventPublisher, _silent
from ..parser.extraction import Extractor
from ..parser.storage import FilesystemStore, Store
from ..normalizer.config import NormalizerConfig
from ..normalizer.normalizer import Normalizer

from .config import ProcessingConfig
from .corpus import DocRef, load_manifest, pending, save_manifest


@dataclass
class DocResult:
    docref: DocRef
    status: str = "ok"           # ok | failed | skipped
    document_id: str = ""
    result_type: str = ""
    error: str = ""
    ms: float = 0.0


@dataclass
class BatchReport:
    total: int = 0
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    elapsed_ms: float = 0.0
    per_format: dict[str, int] = field(default_factory=dict)
    ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    manifest_path: str = ""


class ParseNormalizePipeline:
    """One batch worker's unit of work: parse -> normalize -> persist."""

    def __init__(self, store: Store):
        from ..parser.config import default_config as parser_cfg

        # batch pipelines emit to a broker, not stdout (see _silent)
        self.extractor = Extractor(parser_cfg(), store, events=EventPublisher(sink=_silent))
        self.normalizer = Normalizer(NormalizerConfig())
        self.store = store

    def process(self, ref: DocRef) -> DocResult:
        t0 = time.time()
        try:
            data = open(ref.path, "rb").read()
        except OSError as e:
            return DocResult(ref, status="failed", error=str(e), ms=(time.time()-t0)*1000)
        try:
            po = self.extractor.extract(data, filename=ref.name)
            if not po.ok:
                return DocResult(ref, status="failed", error=po.status, ms=(time.time()-t0)*1000)
            normalized = self.normalizer.normalize(po.document)
            self.store.put_normalized(po.document_id, normalized)
            return DocResult(
                ref, status="ok", document_id=po.document_id,
                result_type=po.detected.slug if po.detected else "?",
                ms=(time.time()-t0)*1000,
            )
        except Exception as e:
            return DocResult(ref, status="failed", error=f"{type(e).__name__}: {e}",
                             ms=(time.time()-t0)*1000)


class BatchWorker:
    def __init__(self, config: ProcessingConfig, pipeline: ParseNormalizePipeline):
        self.config = config
        self.pipeline = pipeline
        self._lock = threading.Lock()
        self._manifest: set[str] = set()
        self._report = BatchReport(manifest_path=config.manifest_path)
        self._flush_every = 256

    def run(self, refs: list[DocRef]) -> BatchReport:
        t_start = time.time()
        if self.config.ocr_warm:
            parser_ocr.engine_available()          # preload once for the pool

        # fresh report + manifest per run (a worker may be reused across runs)
        self._report = BatchReport(manifest_path=self.config.manifest_path)
        self._manifest = load_manifest(self.config.manifest_path)
        todo = [r for r in refs if pending(r, self._manifest)]
        self._report.total = len(refs)
        self._report.skipped = len(refs) - len(todo)

        if todo:
            with ThreadPoolExecutor(max_workers=self.config.concurrency) as pool:
                futures = [pool.submit(self._run_with_retries, r) for r in todo]
                for fut in futures:
                    self._record(fut.result())
        self._flush()
        self._report.elapsed_ms = (time.time() - t_start) * 1000
        return self._report

    def _run_with_retries(self, ref: DocRef) -> DocResult:
        for attempt in range(self.config.max_retries):
            res = self.pipeline.process(ref)
            if res.status == "ok":
                return res
            if attempt < self.config.max_retries - 1:
                time.sleep(self.config.base_backoff_s * (2 ** attempt))
        return res

    def _record(self, res: DocResult) -> None:
        with self._lock:
            r = self._report
            if res.status == "ok":
                r.ok += 1
                r.ids.append(res.document_id)
                r.per_format[res.result_type] = r.per_format.get(res.result_type, 0) + 1
                self._manifest.add(res.docref.sha256)
            elif res.status == "failed":
                r.failed += 1
                r.errors.append(f"{res.docref.name}: {res.error}")
            # skipped counted at start
            if (r.ok + r.failed) % self._flush_every == 0:
                self._flush()

    def _flush(self) -> None:
        with self._lock:
            save_manifest(self.config.manifest_path, self._manifest)


def build_default_pipeline(store_root: str) -> ParseNormalizePipeline:
    return ParseNormalizePipeline(FilesystemStore(store_root))