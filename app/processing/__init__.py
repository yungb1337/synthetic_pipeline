"""Synthetic Data Factory — Workflow / Batch Execution layer.

Previously the pipeline was single-document and interactive. Enterprise scale
(thousands → millions of documents) requires a *batch* execution boundary:

  * scan a corpus once, hash each file, and keep an idempotent "done"
    manifest so re-runs are incremental (a million-doc corpus resumes cheaply),
  * run the (deterministic) Parse → Normalize pipeline in parallel worker pools
    with retries and per-item outcomes,
  * warm expensive model engines (OCR) once per process instead of per call,
  * leave a batched-embedding seam so chunk→embed is a batch model call.

This is the "worker pools / queues / retries / monitoring" layer from the
architecture brief (SYN2 layer 14, SYN4 "100 workers not 1").
"""

__version__ = "0.1.0"